"""eTIMS Integration module using kra_etims SDK"""
import json
import frappe
from functools import partial
from secrets import token_hex
from datetime import datetime, timedelta
from typing import Dict, Optional
from frappe.model.document import Document
from frappe.integrations.utils import create_request_log
from frappe.utils import random_string

from kra_etims_sdk.auth import AuthClient
from kra_etims_sdk.client import EtimsClient

# Local imports
from ..doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    USER_DOCTYPE_NAME,
)
from ..utils import (
    build_datetime_from_string,
    split_user_email,
    get_curr_env_etims_settings,
    get_first_branch_id,
    update_last_request_date,
)
from .remote_response_status_handlers import (
    customer_branch_details_submission_on_success,
    customer_insurance_details_submission_on_success,
    customer_search_on_success,
    imported_item_submission_on_success,
    imported_items_search_on_success,
    item_composition_submission_on_success,
    item_registration_on_success,
    notices_search_on_success,
    on_error,
    purchase_search_on_success,
    search_branch_request_on_success,
    stock_mvt_search_on_success,
    submit_inventory_on_success,
    user_details_submission_on_success,
)


class EtimsSDKWrapper:
    """Thread-safe wrapper to manage SDK client lifecycle with Frappe settings"""

    @classmethod
    def get_client(cls, company_name: str, vendor: str = "OSCU KRA", branch_id: str = "00") -> EtimsClient:
        """Get or create SDK client for given credentials using Frappe's thread-safe cache"""
        cache_key = f"etims_client:{company_name}:{vendor}:{branch_id}"
        cached_client = frappe.cache().get_value(cache_key)
        
        if cached_client:
            return cached_client
        
        settings = get_curr_env_etims_settings(company_name, vendor, branch_id)
        if not settings:
            frappe.throw(
                f"No eTIMS settings found for company: {company_name}, branch: {branch_id}",
                title="Configuration Error"
            )
        
        # Build SDK configuration from Frappe settings (FIXED: removed trailing spaces in URLs)
        config = {
            'env': 'sbx' if settings.env == "Sandbox" else 'prod',
            'auth': {
                'sbx': {
                    'token_url': 'https://sbx.kra.go.ke/v1/token/generate',
                    'consumer_key': settings.consumer_key,
                    'consumer_secret': settings.consumer_secret,
                },
                'prod': {
                    'token_url': 'https://kra.go.ke/v1/token/generate',
                    'consumer_key': settings.consumer_key,
                    'consumer_secret': settings.consumer_secret,
                }
            },
            'api': {
                'sbx': {'base_url': 'https://etims-api-sbx.kra.go.ke/etims-api'},
                'prod': {'base_url': 'https://etims-api.kra.go.ke/etims-api'}
            },
            'http': {'timeout': 30},
            'oscu': {
                'tin': settings.tin,
                'bhf_id': settings.bhfid,
                'device_serial': settings.dvcsrlno,
                'cmc_key': settings.communication_key or '',
            }
        }
        
        auth = AuthClient(config)
        client = EtimsClient(config, auth)
        
        # Cache client for 55 minutes (just under token expiry)
        frappe.cache().set_value(cache_key, client, expires_in_sec=3300)
        return client


def update_integration_request_status(
    integration_request_name: str,
    status: str,
    output: str | None = None,
    error: str | None = None,
) -> None:
    """Updates an Integration Request record status after eTIMS API call"""
    frappe.db.set_value(
        "Integration Request",
        integration_request_name,
        {
            "status": status,
            "output": output,
            "error": error,
        },
        update_modified=True
    )
    frappe.db.commit()


@frappe.whitelist()
def bulk_submit_sales_invoices(docs_list: str) -> None:
    from ..overrides.server.sales_invoice import on_submit
    data = json.loads(docs_list)
    
    # Optimize: fetch only relevant invoices in one query
    valid_names = {inv.name for inv in frappe.db.get_all(
        "Sales Invoice", 
        {"docstatus": 1, "custom_successfully_submitted": 0, "name": ["in", data]}, 
        ["name"]
    )}
    
    for record in data:
        if record in valid_names:
            doc = frappe.get_doc("Sales Invoice", record)
            on_submit(doc, method=None)


@frappe.whitelist()
def bulk_register_item(docs_list: str) -> None:
    data = json.loads(docs_list)
    
    # Optimize: fetch only relevant items in one query
    valid_names = {item.name for item in frappe.db.get_all(
        "Item", 
        {"custom_item_registered": 0, "name": ["in", data]}, 
        ["name"]
    )}
    
    for record in data:
        if record in valid_names:
            process_single_item(record)


@frappe.whitelist()
def process_single_item(record: str) -> None:
    """Process a single item for registration using SDK"""
    item = frappe.get_doc("Item", record)
    valuation_rate = item.valuation_rate if item.valuation_rate is not None else 0

    request_data = {
        "name": item.name,
        "company_name": frappe.defaults.get_user_default("Company"),
        "itemCd": item.custom_item_code_etims,
        "itemClsCd": item.custom_item_classification,
        "itemTyCd": item.custom_product_type,
        "itemNm": item.item_name,
        "temStdNm": None,
        "orgnNatCd": item.custom_etims_country_of_origin_code,
        "pkgUnitCd": item.custom_packaging_unit_code,
        "qtyUnitCd": item.custom_unit_of_quantity_code,
        "taxTyCd": item.get("custom_taxation_type", "B"),
        "btchNo": None,
        "bcd": None,
        "dftPrc": round(valuation_rate, 2),
        "grpPrcL1": None,
        "grpPrcL2": None,
        "grpPrcL3": None,
        "grpPrcL4": None,
        "grpPrcL5": None,
        "addInfo": None,
        "sftyQty": None,
        "isrcAplcbYn": "Y",
        "useYn": "Y",
        "regrId": split_user_email(item.owner),
        "regrNm": item.owner,
        "modrId": split_user_email(item.modified_by),
        "modrNm": item.modified_by,
    }

    perform_item_registration(json.dumps(request_data))


@frappe.whitelist()
def perform_customer_search(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Search customer details using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]

    def _search():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            # Create integration request BEFORE API call
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:select_customer",
                reference_docname=data["name"],
                reference_doctype="Customer",
            )
            response = client.select_customer({"custmTin": data["tax_id"]})
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                customer_search_on_success(response, document_name=data["name"])
                frappe.msgprint("Customer search completed successfully")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/CustSearchReq",
                    doctype="Customer",
                    document_name=data["name"],
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/CustSearchReq", doctype="Customer", document_name=data["name"])
            frappe.log_error(title="eTIMS Customer Search Error", message=str(e))

    frappe.enqueue(
        _search,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{data['name']}_customer_search",
    )


@frappe.whitelist()
def perform_item_registration(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Register item using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data.pop("company_name")
    item_name = data["name"]

    def _register():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            # Create integration request BEFORE API call
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:save_item",
                reference_docname=item_name,
                reference_doctype="Item",
            )
            # Prepare payload matching SDK expectations
            payload = {
                "itemCd": data["itemCd"],
                "itemClsCd": data["itemClsCd"],
                "itemTyCd": data["itemTyCd"],
                "itemNm": data["itemNm"],
                "orgnNatCd": data["orgnNatCd"],
                "pkgUnitCd": data["pkgUnitCd"],
                "qtyUnitCd": data["qtyUnitCd"],
                "taxTyCd": data["taxTyCd"],
                "dftPrc": data["dftPrc"],
                "isrcAplcbYn": data["isrcAplcbYn"],
                "useYn": data["useYn"],
                "regrId": data["regrId"],
                "regrNm": data["regrNm"],
                "modrId": data["modrId"],
                "modrNm": data["modrNm"],
            }
            response = client.save_item(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                item_registration_on_success(response, document_name=item_name)
                update_last_request_date(response.get("resultDt", ""), "/ItemSaveReq")
                frappe.msgprint("Item registered successfully")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/ItemSaveReq",
                    doctype="Item",
                    document_name=item_name,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/ItemSaveReq", doctype="Item", document_name=item_name)
            frappe.log_error(title="eTIMS Item Registration Error", message=str(e))

    frappe.enqueue(
        _register,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{item_name}_register_item",
    )


@frappe.whitelist()
def send_insurance_details(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Submit insurance details using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]
    doc_name = data["name"]
    branch_id = data.get("branch_id", "00")

    def _submit():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:save_branch_insurance",
                reference_docname=doc_name,
                reference_doctype="Customer",
            )
            payload = {
                "isrccCd": data["insurance_code"],
                "isrccNm": data["insurance_name"],
                "isrcRt": round(data["premium_rate"], 0),
                "useYn": "Y",
                "regrNm": data["registration_id"],
                "regrId": split_user_email(data["registration_id"]),
                "modrNm": data["modifier_id"],
                "modrId": split_user_email(data["modifier_id"]),
            }
            response = client.save_branch_insurance(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                customer_insurance_details_submission_on_success(response, document_name=doc_name)
                frappe.msgprint("Insurance details submitted successfully")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/BhfInsuranceSaveReq",
                    doctype="Customer",
                    document_name=doc_name,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/BhfInsuranceSaveReq", doctype="Customer", document_name=doc_name)
            frappe.log_error(title="eTIMS Insurance Submission Error", message=str(e))

    frappe.enqueue(
        _submit,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{doc_name}_submit_insurance_information",
    )


@frappe.whitelist()
def send_branch_customer_details(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Submit branch customer details using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]
    doc_name = data["name"]
    branch_id = data.get("branch_id", "00")

    def _submit():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:save_branch_customer",
                reference_docname=doc_name,
                reference_doctype="Customer",
            )
            payload = {
                "custNo": data["name"][:14],
                "custTin": data["customer_pin"],
                "custNm": data["customer_name"],
                "adrs": None,
                "telNo": None,
                "email": None,
                "faxNo": None,
                "useYn": "Y",
                "remark": None,
                "regrNm": data["registration_id"],
                "regrId": split_user_email(data["registration_id"]),
                "modrNm": data["modifier_id"],
                "modrId": split_user_email(data["modifier_id"]),
            }
            response = client.save_branch_customer(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                customer_branch_details_submission_on_success(response, document_name=doc_name)
                frappe.msgprint("Customer branch details submitted successfully")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/BhfCustSaveReq",
                    doctype="Customer",
                    document_name=doc_name,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/BhfCustSaveReq", doctype="Customer", document_name=doc_name)
            frappe.log_error(title="eTIMS Branch Customer Submission Error", message=str(e))

    frappe.enqueue(
        _submit,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{doc_name}_submit_customer_branch_details",
    )


@frappe.whitelist()
def save_branch_user_details(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Submit branch user details using SDK with SECURE password generation"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]
    doc_name = data["name"]
    branch_id = data.get("branch_id", "00")
    
    # Generate secure random password (12 chars)
    secure_password = random_string(12)

    def _submit():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
            integration_request = create_request_log(
                data={**data, "pwd": "[REDACTED]"},  # Don't log actual password
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:save_branch_user",
                reference_docname=doc_name,
                reference_doctype=USER_DOCTYPE_NAME,
            )
            payload = {
                "userId": data["user_id"],
                "userNm": data["full_names"],
                "pwd": secure_password,  # ✅ SECURE PASSWORD
                "adrs": None,
                "cntc": None,
                "authCd": None,
                "remark": None,
                "useYn": "Y",
                "regrNm": data["registration_id"],
                "regrId": split_user_email(data["registration_id"]),
                "modrNm": data["modifier_id"],
                "modrId": split_user_email(data["modifier_id"]),
            }
            response = client.save_branch_user(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                user_details_submission_on_success(response, document_name=doc_name)
                
                # Notify user of generated password
                frappe.msgprint(
                    f"""Branch user details submitted successfully.<br>
                    <b>Generated Password:</b> {secure_password}<br>
                    <span style='color:orange'>⚠️ User must change password on first login</span>""",
                    indicator="green"
                )
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/BhfUserSaveReq",
                    doctype=USER_DOCTYPE_NAME,
                    document_name=doc_name,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/BhfUserSaveReq", doctype=USER_DOCTYPE_NAME, document_name=doc_name)
            frappe.log_error(title="eTIMS Branch User Submission Error", message=str(e))

    frappe.enqueue(
        _submit,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{doc_name}_send_branch_user_information",
    )


@frappe.whitelist()
def perform_import_item_search(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Search imported items using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]
    branch_id = data.get("branch_id", "00")

    def _search():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:select_imported_items",
                reference_docname=None,
                reference_doctype="Item",
            )
            # Get last request date from routes table or default to 1 year ago
            last_req_dt = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d%H%M%S")
            response = client.select_imported_items({"lastReqDt": last_req_dt})
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                imported_items_search_on_success(response)
                if "resultDt" in response:
                    update_last_request_date(response["resultDt"], "/ImportItemSearchReq")
                frappe.msgprint("Imported items search completed")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/ImportItemSearchReq",
                    doctype="Item",
                    document_name=None,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/ImportItemSearchReq", doctype="Item", document_name=None)
            frappe.log_error(title="eTIMS Imported Items Search Error", message=str(e))

    frappe.enqueue(
        _search,
        is_async=True,
        queue="default",
        timeout=300,
        job_name="imported_items_search",
    )


@frappe.whitelist()
def perform_import_item_search_all_branches() -> None:
    """Perform imported item search for all active branches"""
    all_credentials = frappe.get_all(
        SETTINGS_DOCTYPE_NAME,
        filters={"is_active": 1},
        fields=["name", "bhfid", "company"],
    )
    for credential in all_credentials:
        request_data = json.dumps({
            "company_name": credential.company,
            "branch_id": credential.bhfid
        })
        perform_import_item_search(request_data)


@frappe.whitelist()
def submit_inventory(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Submit stock master using SDK"""
    data: Dict = json.loads(request_data)
    company_name = frappe.defaults.get_user_default("Company")
    doc_name = data["name"]
    branch_id = data["branch_id"]

    def _submit():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:save_stock_master",
                reference_docname=doc_name,
                reference_doctype="Stock Ledger Entry",
            )
            payload = {
                "itemCd": data["item_code"],
                "rsdQty": data["residual_qty"],
                "regrId": split_user_email(data["owner"]),
                "regrNm": data["owner"],
                "modrId": split_user_email(data["owner"]),
                "modrNm": data["owner"],
            }
            response = client.save_stock_master(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                submit_inventory_on_success(response, document_name=doc_name)
                frappe.msgprint("Inventory submitted successfully")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/StockMasterSaveReq",
                    doctype="Stock Ledger Entry",
                    document_name=doc_name,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/StockMasterSaveReq", doctype="Stock Ledger Entry", document_name=doc_name)
            frappe.log_error(title="eTIMS Inventory Submission Error", message=str(e))

    frappe.enqueue(
        _submit,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{doc_name}_submit_inventory",
    )


@frappe.whitelist()
def search_branch_request(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Search branches using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]

    def _search():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:select_branches",
                reference_docname=None,
                reference_doctype="Branch",
            )
            # Use fixed date as per KRA requirements for initial sync
            response = client.select_branches({"lastReqDt": "20240101000000"})
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                search_branch_request_on_success(response)
                frappe.msgprint("Branch search completed successfully")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/BhfSearchReq",
                    doctype="Branch",
                    document_name=None,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/BhfSearchReq", doctype="Branch", document_name=None)
            frappe.log_error(title="eTIMS Branch Search Error", message=str(e))

    _search()  # Run synchronously as it's typically a manual operation


@frappe.whitelist()
def send_imported_item_request(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Update imported item using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]
    doc_name = data["name"]

    def _update():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:update_imported_item",
                reference_docname=doc_name,
                reference_doctype="Item",
            )
            declaration_date = build_datetime_from_string(
                data["declaration_date"], "%Y-%m-%d %H:%M:%S.%f"
            ).strftime("%Y%m%d")
            payload = {
                "taskCd": data["task_code"],
                "dclDe": declaration_date,
                "itemSeq": data["item_sequence"],
                "hsCd": data["hs_code"],
                "itemClsCd": data["item_classification_code"],
                "itemCd": data["item_code"],
                "imptItemSttsCd": data["import_item_status"],
                "remark": None,
                "modrNm": data["modified_by"],
                "modrId": split_user_email(data["modified_by"]),
            }
            response = client.update_imported_item(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                imported_item_submission_on_success(response, document_name=doc_name)
                frappe.msgprint("Imported item updated successfully")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/ImportItemUpdateReq",
                    doctype="Item",
                    document_name=doc_name,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/ImportItemUpdateReq", doctype="Item", document_name=doc_name)
            frappe.log_error(title="eTIMS Imported Item Update Error", message=str(e))

    frappe.enqueue(
        _update,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{doc_name}_submit_imported_item",
    )


@frappe.whitelist()
def perform_notice_search(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Search notices using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]

    def _search():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:select_notice_list",
                reference_docname=data.get("name"),
                reference_doctype=SETTINGS_DOCTYPE_NAME,
            )
            last_req_dt = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d%H%M%S")
            response = client.select_notice_list({"lastReqDt": last_req_dt})
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                notices_search_on_success(response)
                if "resultDt" in response:
                    update_last_request_date(response["resultDt"], "/NoticeSearchReq")
                frappe.msgprint("Notice search completed successfully")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/NoticeSearchReq",
                    doctype=SETTINGS_DOCTYPE_NAME,
                    document_name=data.get("name"),
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/NoticeSearchReq", doctype=SETTINGS_DOCTYPE_NAME, document_name=data.get("name"))
            frappe.log_error(title="eTIMS Notice Search Error", message=str(e))

    frappe.enqueue(
        _search,
        is_async=True,
        queue="default",
        timeout=300,
        job_name="notice_search",
    )


@frappe.whitelist()
def perform_stock_movement_search(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Search stock movements using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]
    branch_id = data["branch_id"]

    def _search():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:select_stock_movements",
                reference_docname=None,
                reference_doctype=None,
            )
            last_req_dt = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d%H%M%S")
            response = client.select_stock_movements({"lastReqDt": last_req_dt})
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                stock_mvt_search_on_success(response)
                if "resultDt" in response:
                    update_last_request_date(response["resultDt"], "/StockMoveReq")
                frappe.msgprint("Stock movement search completed")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/StockMoveReq",
                    doctype=None,
                    document_name=None,
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/StockMoveReq", doctype=None, document_name=None)
            frappe.log_error(title="eTIMS Stock Movement Search Error", message=str(e))

    frappe.enqueue(
        _search,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=token_hex(16),  # Reduced from 100 to safe 32 chars
    )


@frappe.whitelist()
def perform_stock_movement_search_all_branches() -> None:
    """Perform stock movement search for all active branches"""
    all_credentials = frappe.get_all(
        SETTINGS_DOCTYPE_NAME,
        filters={"is_active": 1},
        fields=["name", "bhfid", "company"],
    )
    for credential in all_credentials:
        request_data = json.dumps({
            "company_name": credential.company,
            "branch_id": credential.bhfid
        })
        perform_stock_movement_search(request_data)


@frappe.whitelist()
def submit_item_composition(request_data: str, vendor: str = "OSCU KRA") -> None:
    """Submit item composition (BOM) using SDK"""
    data: Dict = json.loads(request_data)
    company_name = data["company_name"]
    doc_name = data["name"]

    def _submit():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            integration_request = create_request_log(
                data=data,
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:save_item_composition",
                reference_docname=doc_name,
                reference_doctype="BOM",
            )
            # Check if manufactured item is registered
            manufactured_item = frappe.get_value(
                "Item",
                {"name": data["item_name"]},
                ["custom_item_registered", "name"],
                as_dict=True,
            )
            if not manufactured_item or not manufactured_item.custom_item_registered:
                frappe.throw(
                    f"Please register item: <b>{manufactured_item.name}</b> first to proceed.",
                    title="Integration Error",
                )
            
            # Verify all component items are registered (optimized lookup)
            component_codes = [item["item_code"] for item in data["items"]]
            registered_codes = set(frappe.db.get_list(
                "Item",
                filters={"item_code": ["in", component_codes], "custom_item_registered": 1},
                pluck="item_code"
            ))
            
            missing_codes = [code for code in component_codes if code not in registered_codes]
            if missing_codes:
                frappe.throw(
                    f"Items not registered: <b>{', '.join(missing_codes)}</b>. "
                    "Ensure ALL component items are registered first.",
                    title="Integration Error",
                )
            
            # Submit composition for each component
            for item in data["items"]:
                payload = {
                    "itemCd": data["item_code"],
                    "cpstItemCd": item["item_code"],
                    "cpstQty": item["qty"],
                    "regrId": split_user_email(data["registration_id"]),
                    "regrNm": data["registration_id"],
                }
                response = client.save_item_composition(payload)
                if response.get("resultCd") != "000":
                    frappe.throw(
                        f"Failed to submit composition for {item['item_code']}: "
                        f"{response.get('resultMsg', 'Unknown error')}",
                        title="Integration Error",
                    )
            
            update_integration_request_status(
                integration_request.name,
                status="Completed",
                output="Item composition submitted successfully",
                error=None
            )
            item_composition_submission_on_success(None, document_name=doc_name)
            frappe.msgprint("Item composition submitted successfully")
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/SaveItemComposition", doctype="BOM", document_name=doc_name)
            frappe.log_error(title="eTIMS Item Composition Error", message=str(e))

    frappe.enqueue(
        _submit,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{doc_name}_submit_item_composition",
    )


@frappe.whitelist()
def ping_server(request_data: str) -> None:
    """Check server connectivity using simple HTTP HEAD request (more reliable than dummy auth)"""
    import aiohttp
    import asyncio
    
    url = json.loads(request_data)["server_url"]
    
    async def check_connectivity():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=5) as resp:
                    return resp.status == 200
        except:
            return False
    
    try:
        is_online = asyncio.run(check_connectivity())
        frappe.msgprint("The Server is Online" if is_online else "The Server is Offline")
    except Exception as e:
        frappe.msgprint("The Server is Offline")
        frappe.log_error(title="Server Ping Error", message=str(e))


@frappe.whitelist()
def create_stock_entry_from_stock_movement(request_data: str) -> None:
    data = json.loads(request_data)
    
    # Create missing items
    for item in data["items"]:
        if not frappe.db.exists("Item", item["item_name"], cache=False):
            create_item(item)
    
    # Get target branch from Company settings (replaces hardcoded "01")
    company = frappe.defaults.get_user_default("Company")
    target_branch = frappe.db.get_value(
        "Company", 
        company, 
        "default_transfer_branch"
    ) or get_first_branch_id()  # Fallback to first branch
    
    # Create stock entry
    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.stock_entry_type = "Material Transfer"
    stock_entry.set("items", [])
    
    source_warehouse = frappe.get_value(
        "Warehouse",
        {"custom_branch": data["branch_id"]},
        ["name"],
        as_dict=True,
    )
    
    target_warehouse = frappe.get_value(
        "Warehouse",
        {"custom_branch": target_branch},
        ["name"],
        as_dict=True,
    )
    
    if not source_warehouse or not target_warehouse:
        frappe.throw(
            f"Warehouse not found for branch transfer: "
            f"{data['branch_id']} → {target_branch}",
            title="Configuration Error"
        )
    
    for item in data["items"]:
        stock_entry.append(
            "items",
            {
                "s_warehouse": source_warehouse.name,
                "t_warehouse": target_warehouse.name,
                "item_code": item["item_name"],
                "qty": item["quantity"],
            },
        )
    
    stock_entry.save()
    frappe.msgprint(f"Stock Entry {stock_entry.name} created successfully")


def create_item(item: dict | frappe._dict) -> Document:
    """Create item from imported purchase/stock movement data"""
    item_code = item.get("item_code", None)
    
    # Check if item already exists by name or code
    existing = frappe.db.exists("Item", {"item_code": item["item_name"]})
    if existing:
        return frappe.get_doc("Item", existing)
    
    new_item = frappe.new_doc("Item")
    new_item.is_stock_item = 0  # Default to 0
    new_item.item_code = item["item_name"]
    new_item.item_name = item.get("item_description", item["item_name"])
    new_item.item_group = "All Item Groups"
    new_item.custom_item_classification = item["item_classification_code"]
    new_item.custom_packaging_unit_code = item["packaging_unit_code"]
    new_item.custom_unit_of_quantity_code = (
        item.get("quantity_unit_code", None) or item["unit_of_quantity_code"]
    )
    new_item.custom_taxation_type = item["taxation_type_code"]
    
    # Set country of origin from item code prefix
    if item_code:
        country_doc = frappe.db.exists(
            COUNTRIES_DOCTYPE_NAME,
            {"code": item_code[:2]}
        )
        if country_doc:
            new_item.custom_etims_country_of_origin_code = item_code[:2]
    
    new_item.custom_product_type = item_code[2:3] if item_code else None
    
    # Determine stock item status based on product type
    if item_code and int(item_code[2:3]) != 3:
        new_item.is_stock_item = 1
    
    new_item.custom_item_code_etims = item["item_code"]
    new_item.valuation_rate = item.get("unit_price", 0)
    
    # Handle imported items
    if "imported_item" in item:
        new_item.is_stock_item = 1
        new_item.custom_referenced_imported_item = item["imported_item"]
    
    new_item.insert(ignore_mandatory=True, ignore_if_duplicate=True)
    
    # Auto-register if classification is set
    if new_item.custom_item_classification:
        process_single_item(new_item.name)
    
    return new_item


def validate_mapping_and_registration_of_items(items):
    """Validate items against registered imports"""
    for item in items:
        task_code = item.get("task_code") or item.get("item_name")
        matched_items = frappe.get_all(
            "Item",
            filters={"custom_referenced_imported_item": task_code},
            fields=["name", "item_name", "item_code"]
        )
        if matched_items:
            item_name = matched_items[0].name
            from kra_etims_frappe.kra_etims_frappe.overrides.server.purchase_invoice import validation_message
            validation_message(item_name)