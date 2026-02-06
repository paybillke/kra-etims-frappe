import json

import frappe
import frappe.defaults
from frappe.model.document import Document
from frappe.integrations.utils import create_request_log

from ..apis.remote_response_status_handlers import on_error
from ..doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
    PACKAGING_UNIT_DOCTYPE_NAME,
    SETTINGS_DOCTYPE_NAME,
    TAXATION_TYPE_DOCTYPE_NAME,
    UNIT_OF_QUANTITY_DOCTYPE_NAME,
)
from ..overrides.server.stock_ledger_entry import on_update
from ..utils import (
    get_curr_env_etims_settings,
    update_last_request_date
)
from ..apis.apis import  (
    EtimsSDKWrapper,
    update_integration_request_status
)


def refresh_notices() -> None:
    from ..apis.apis import perform_notice_search

    company = frappe.defaults.get_user_default("Company")

    perform_notice_search(json.dumps({"company_name": company}))


def send_sales_invoices_information() -> None:
    from ..overrides.server.sales_invoice import on_submit

    all_submitted_unsent: list[Document] = frappe.get_all(
        "Sales Invoice", {"docstatus": 1, "custom_successfully_submitted": 0, "is_opening":"No"}, ["name"]
    )  

    if all_submitted_unsent:
        for sales_invoice in all_submitted_unsent:
            doc = frappe.get_doc(
                "Sales Invoice", sales_invoice.name, for_update=False
            )  

            try:
                on_submit(
                    doc, method=None
                )  

            except TypeError:
                continue


def send_pos_invoices_information() -> None:
    from ..overrides.server.sales_invoice import on_submit

    all_pending_pos_invoices: list[Document] = frappe.get_all(
        "POS Invoice", {"docstatus": 1, "custom_successfully_submitted": 0}, ["name"]
    )

    if all_pending_pos_invoices:
        for pos_invoice in all_pending_pos_invoices:
            doc = frappe.get_doc(
                "POS Invoice", pos_invoice.name, for_update=False
            )  # Refetch to get the document representation of the record

            try:
                on_submit(
                    doc, method=None
                )  # Delegate to the on_submit method for sales invoices

            except TypeError:
                continue


def send_stock_information() -> None:
    all_stock_ledger_entries: list[Document] = frappe.get_all(
        "Stock Ledger Entry",
        {"docstatus": 1, "custom_submitted_successfully": 0},
        ["name"],
    )
    for entry in all_stock_ledger_entries:
        doc = frappe.get_doc(
            "Stock Ledger Entry", entry.name, for_update=False
        )  

        try:
            on_update(
                doc, method=None
            )  

        except TypeError:
            continue


def send_purchase_information() -> None:
    from ..overrides.server.purchase_invoice import on_submit

    all_submitted_purchase_invoices: list[Document] = frappe.get_all(
        "Purchase Invoice",
        {"docstatus": 1, "custom_submitted_successfully": 0},
        ["name"],
    )

    for invoice in all_submitted_purchase_invoices:
        doc = frappe.get_doc(
            "Purchase Invoice", invoice.name, for_update=False
        )  # Refetch to get the document representation of the record

        try:
            on_submit(doc, method=None)

        except TypeError:
            continue


def send_item_inventory_information() -> None:
    from ..apis.apis import submit_inventory

    query = """
        SELECT sle.name as name,
            sle.owner,
            sle.custom_submitted_successfully,
            sle.custom_inventory_submitted_successfully,
            qty_after_transaction as residual_qty,
            sle.warehouse,
            w.custom_branch as branch_id,
            i.item_code as item,
            custom_item_code_etims as item_code
        FROM `tabStock Ledger Entry` sle
            INNER JOIN tabItem i ON sle.item_code = i.item_code
            INNER JOIN tabWarehouse w ON sle.warehouse = w.name
        WHERE sle.custom_submitted_successfully = '1'
            AND sle.custom_inventory_submitted_successfully = '0'
        ORDER BY sle.creation DESC;
        """

    sles = frappe.db.sql(query, as_dict=True)

    for stock_ledger in sles:
        response = json.dumps(stock_ledger)

        try:
            submit_inventory(response)

        except Exception as error:
            # TODO: Suspicious looking type(error)
            frappe.throw("Error Encountered", type(error), title="Error")


@frappe.whitelist()
def refresh_code_lists(vendor: str = "OSCU KRA") -> str:
    """Refresh all eTIMS code lists using SDK (async)"""
    company_name: str | None = frappe.defaults.get_user_default("Company")
    
    if not company_name:
        frappe.throw("No active company found. Please set a default company.", title="Configuration Error")
    
    def _refresh_codes():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            integration_request = create_request_log(
                data={"purpose": "code_lists_refresh"},
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:select_code_lists",
                reference_docname=None,
                reference_doctype=None,
            )
            
            # Hardcoded date to fetch ALL code lists (KRA requirement for initial sync)
            payload = {"lastReqDt": "20200101000000"}
            response = client.select_code_list(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                # Execute all code list updaters
                run_updater_functions(response)
                frappe.msgprint("✅ Code lists refreshed successfully", indicator="green")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/CodeSearchReq",
                    doctype=None,
                    document_name=None,
                )
                frappe.log_error(
                    title="eTIMS Code Lists Refresh Failed",
                    message=f"Response: {json.dumps(response)}"
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/CodeSearchReq", doctype=None, document_name=None)
            frappe.log_error(title="eTIMS Code Lists Refresh Error", message=str(e))
            frappe.msgprint("❌ Code lists refresh failed. Check Error Log.", indicator="red")

    frappe.enqueue(
        _refresh_codes,
        is_async=True,
        queue="long",
        timeout=1800,  # 30 minutes for large code lists
        job_name="etims_refresh_code_lists",
    )
    return "Code lists refresh job enqueued"


@frappe.whitelist()
def get_item_classification_codes(vendor: str = "OSCU KRA") -> str:
    """Fetch and update item classification codes using SDK (async)"""
    company_name: str | None = frappe.defaults.get_user_default("Company")
    
    if not company_name:
        frappe.throw("No active company found. Please set a default company.", title="Configuration Error")
    
    def _fetch_classifications():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            integration_request = create_request_log(
                data={"purpose": "item_classification_refresh"},
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:select_item_classifications",
                reference_docname=None,
                reference_doctype=SETTINGS_DOCTYPE_NAME,
            )
            
            # Hardcoded date to fetch ALL classifications (KRA requirement)
            payload = {"lastReqDt": "20230101000000"}
            response = client.select_item_classes(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                update_item_classification_codes(response)
                if "resultDt" in response:
                    update_last_request_date(response["resultDt"], "/ItemClsSearchReq")
                frappe.msgprint("✅ Item classification codes updated successfully", indicator="green")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/ItemClsSearchReq",
                    doctype=SETTINGS_DOCTYPE_NAME,
                    document_name=None,
                )
                frappe.log_error(
                    title="eTIMS Item Classification Refresh Failed",
                    message=f"Response: {json.dumps(response)}"
                )
        except Exception as e:
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            on_error(str(e), url="/ItemClsSearchReq", doctype=SETTINGS_DOCTYPE_NAME, document_name=None)
            frappe.log_error(title="eTIMS Item Classification Error", message=str(e))
            frappe.msgprint("❌ Item classification refresh failed. Check Error Log.", indicator="red")

    frappe.enqueue(
        _fetch_classifications,
        is_async=True,
        queue="long",
        timeout=1200,  # 20 minutes
        job_name="etims_refresh_item_classifications",
    )
    return "Item classification refresh job enqueued"


@frappe.whitelist()
def get_item_classification_codes(vendor: str = "OSCU KRA") -> str:
    """Fetch and update item classification codes using SDK (async)"""
    company_name: str | None = frappe.defaults.get_user_default("Company")
    
    if not company_name:
        frappe.throw("No active company found. Please set a default company.", title="Configuration Error")
    
    def _fetch_classifications():
        try:
            client = EtimsSDKWrapper.get_client(company_name, vendor)
            integration_request = create_request_log(
                data={"purpose": "item_classification_refresh"},
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:select_item_classifications",
                reference_docname=None,
                reference_doctype=SETTINGS_DOCTYPE_NAME,
            )
            
            # Hardcoded date to fetch ALL classifications (KRA requirement for initial sync)
            payload = {"lastReqDt": "20230101000000"}
            response = client.select_item_classes(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                # Update local item classification codes
                update_item_classification_codes(response)
                
                # Update last request date for future incremental syncs
                if "resultDt" in response:
                    update_last_request_date(response["resultDt"], "/ItemClsSearchReq")
                
                frappe.msgprint("✅ Item classification codes updated successfully", indicator="green")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/ItemClsSearchReq",
                    doctype=SETTINGS_DOCTYPE_NAME,
                    document_name=None,
                )
                frappe.log_error(
                    title="eTIMS Item Classification Refresh Failed",
                    message=f"Response: {json.dumps(response)}"
                )
        except Exception as e:
            # Handle integration request cleanup if it was created
            if 'integration_request' in locals():
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=str(e)
                )
            # Call error handler and log
            on_error(str(e), url="/ItemClsSearchReq", doctype=SETTINGS_DOCTYPE_NAME, document_name=None)
            frappe.log_error(title="eTIMS Item Classification Error", message=str(e))
            frappe.msgprint("❌ Item classification refresh failed. Check Error Log.", indicator="red")

    # Enqueue to long queue for potentially large data sets
    frappe.enqueue(
        _fetch_classifications,
        is_async=True,
        queue="long",
        timeout=1200,  # 20 minutes timeout
        job_name="etims_refresh_item_classifications",
    )
    
    return "Item classification refresh job enqueued"


def run_updater_functions(response: dict) -> None:
    for class_list in response["data"]["clsList"]:
        if class_list["cdClsNm"] == "Quantity Unit":
            update_unit_of_quantity(class_list)

        if class_list["cdClsNm"] == "Taxation Type":
            update_taxation_type(class_list)

        if class_list["cdClsNm"] == "Packing Unit":
            update_packaging_units(class_list)

        if class_list["cdClsNm"] == "Country":
            update_countries(class_list)


def update_unit_of_quantity(data: dict) -> None:
    doc: Document | None = None

    for unit_of_quantity in data["dtlList"]:
        try:
            doc = frappe.get_doc(UNIT_OF_QUANTITY_DOCTYPE_NAME, unit_of_quantity["cd"])

        except frappe.DoesNotExistError:
            doc = frappe.new_doc(UNIT_OF_QUANTITY_DOCTYPE_NAME)

        finally:
            doc.code = unit_of_quantity["cd"]
            doc.sort_order = unit_of_quantity["srtOrd"]
            doc.code_name = unit_of_quantity["cdNm"]
            doc.code_description = unit_of_quantity["cdDesc"]

            doc.save()

    frappe.db.commit()


def update_taxation_type(data: dict) -> None:
    doc: Document | None = None

    for taxation_type in data["dtlList"]:
        try:
            doc = frappe.get_doc(TAXATION_TYPE_DOCTYPE_NAME, taxation_type["cd"])

        except frappe.DoesNotExistError:
            doc = frappe.new_doc(TAXATION_TYPE_DOCTYPE_NAME)

        finally:
            doc.cd = taxation_type["cd"]
            doc.cdnm = taxation_type["cdNm"]
            doc.cddesc = taxation_type["cdDesc"]
            doc.useyn = 1 if taxation_type["useYn"] == "Y" else 0
            doc.srtord = taxation_type["srtOrd"]
            doc.userdfncd1 = taxation_type["userDfnCd1"]
            doc.userdfncd2 = taxation_type["userDfnCd2"]
            doc.userdfncd3 = taxation_type["userDfnCd3"]

            doc.save()

    frappe.db.commit()


def update_packaging_units(data: dict) -> None:
    doc: Document | None = None

    for packaging_unit in data["dtlList"]:
        try:
            doc = frappe.get_doc(PACKAGING_UNIT_DOCTYPE_NAME, packaging_unit["cd"])

        except frappe.DoesNotExistError:
            doc = frappe.new_doc(PACKAGING_UNIT_DOCTYPE_NAME)

        finally:
            doc.code = packaging_unit["cd"]
            doc.code_name = packaging_unit["cdNm"]
            doc.sort_order = packaging_unit["srtOrd"]
            doc.code_description = packaging_unit["cdDesc"]

            doc.save()

    frappe.db.commit()


def update_countries(data: dict) -> None:
    doc: Document | None = None

    for country in data["dtlList"]:
        try:
            doc = frappe.get_doc(COUNTRIES_DOCTYPE_NAME, country["cdNm"])

        except frappe.DoesNotExistError:
            doc = frappe.new_doc(COUNTRIES_DOCTYPE_NAME)

        finally:
            doc.code = country["cd"]
            doc.code_name = country["cdNm"]
            doc.sort_order = country["srtOrd"]
            doc.code_description = country["cdDesc"]

            doc.save()

    frappe.db.commit()


def update_item_classification_codes(response: dict) -> None:
    code_list = response["data"]["itemClsList"]
    existing_classifications = {
        classification["name"]: classification
        for classification in frappe.get_all(ITEM_CLASSIFICATIONS_DOCTYPE_NAME, ["*"])
    }

    for item_classification in code_list:
        if item_classification["itemClsCd"] in existing_classifications:
            # Prefer Raw SQL since using the ORM causes performance degradation. Still under investigation
            update_query = f"""
                UPDATE `tab{ITEM_CLASSIFICATIONS_DOCTYPE_NAME}`
                SET itemclscd = '{item_classification["itemClsCd"]}',
                    itemclslvl = '{item_classification["itemClsLvl"]}',
                    itemclsnm = '{item_classification["itemClsNm"].replace("'", " ")}',
                    taxtycd = '{item_classification["taxTyCd"]}',
                    useyn = '{1 if item_classification["useYn"] == "Y" else 0}',
                    mjrtgyn  = '{1 if item_classification["mjrTgYn"] == "Y" else 0}',
                    modified = SYSDATE()
                WHERE name = '{item_classification["itemClsCd"]}';
            """

            frappe.db.sql(update_query)

        else:
            insert_query = f"""
                INSERT INTO `tab{ITEM_CLASSIFICATIONS_DOCTYPE_NAME}`
                    (name, itemclscd, itemclslvl, itemclsnm, taxtycd, useyn, mjrtgyn, creation)
                VALUES
                    ('{item_classification["itemClsCd"]}',
                     '{item_classification["itemClsCd"]}',
                     '{item_classification["itemClsLvl"]}',
                     '{item_classification["itemClsNm"].replace("'", " ")}',
                     '{item_classification["taxTyCd"]}',
                     '{1 if item_classification["useYn"] == "Y" else 0}',
                     '{1 if item_classification["mjrTgYn"] == "Y" else 0}',
                     SYSDATE());
            """

            frappe.db.sql(insert_query)

    frappe.db.commit()
