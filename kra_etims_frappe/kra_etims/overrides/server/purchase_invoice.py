from collections import defaultdict
from functools import partial
import json
import frappe
from frappe.model.document import Document
from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data
from frappe.utils import get_link_to_form 
from frappe.integrations.utils import create_request_log

from ...apis.remote_response_status_handlers import (
	on_error,
	purchase_invoice_submission_on_success,
)
from ...utils import (
	extract_document_series_number,
	quantize_number,
	split_user_email,
	get_taxation_types
)

from ...apis.apis import  (
    EtimsSDKWrapper,
    update_integration_request_status
)


def validate(doc: Document, method: str) -> None:
	if not doc.branch:
		frappe.throw("Please ensure the branch is set before saving the document")
	item_taxes = get_itemised_tax_breakup_data(doc)
	# if not doc.branch:
	#     frappe.throw("Please ensure the branch is set before submitting the document")
	taxes_breakdown = defaultdict(list)
	taxable_breakdown = defaultdict(list)
	if not doc.taxes:
		vat_acct = frappe.get_value(
			"Account", {"account_type": "Tax", "tax_rate": "16"}, ["name"], as_dict=True
		)
		doc.set(
			"taxes",
			[
				{
					"account_head": vat_acct.name,
					"included_in_print_rate": 1,
					"description": vat_acct.name.split("-", 1)[0].strip(),
					"category": "Total",
					"add_deduct_tax": "Add",
					"charge_type": "On Net Total",
				}
			],
		)



def on_submit(doc: Document, method: str | None = None) -> None:
    """Submit purchase invoice to eTIMS when document is submitted"""
    # Validate all items are registered before proceeding
    validate_item_registration(doc.items)
    
    # Only process non-return invoices with stock updates
    if doc.is_return == 0 and doc.update_stock == 1:
        company_name = doc.company
        vendor = "OSCU KRA"
        branch_id = doc.branch or "00"
        
        def _submit_purchase_transaction():
            try:
                # Get SDK client with branch-specific configuration
                client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
                
                # Create integration request BEFORE API call for audit trail
                integration_request = create_request_log(
                    data={"invoice_no": doc.name, "supplier": doc.supplier},
                    is_remote_request=True,
                    service_name="eTIMS",
                    request_headers={},
                    url=f"SDK:{client.config['env']}:save_purchase_transaction",
                    reference_docname=doc.name,
                    reference_doctype="Purchase Invoice",
                )
                
                # Build payload using existing helper function
                payload = build_purchase_invoice_payload(doc)
                
                # Submit to eTIMS via SDK
                response = client.save_purchase(payload)
                
                if response.get("resultCd") == "000":
                    update_integration_request_status(
                        integration_request.name,
                        status="Completed",
                        output=json.dumps(response),
                        error=None
                    )
                    
                    # Call success handler
                    purchase_invoice_submission_on_success(
                        response,
                        document_name=doc.name
                    )
                                        
                    frappe.msgprint(
                        f"✅ Purchase Invoice {doc.name} submitted successfully to eTIMS",
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
                        url="/TrnsPurchaseSaveReq",
                        doctype="Purchase Invoice",
                        document_name=doc.name,
                    )
                    frappe.log_error(
                        title="eTIMS Purchase Invoice Submission Failed",
                        message=f"Invoice: {doc.name}, Supplier: {doc.supplier}, Error: {response.get('resultMsg', 'Unknown')}"
                    )
                    frappe.msgprint(
                        f"❌ Purchase invoice submission failed. Check Error Log.",
                        indicator="red"
                    )
                    
            except Exception as e:
                # Handle integration request cleanup if created
                if 'integration_request' in locals():
                    update_integration_request_status(
                        integration_request.name,
                        status="Failed",
                        output=None,
                        error=str(e)
                    )
                on_error(str(e), url="/TrnsPurchaseSaveReq", doctype="Purchase Invoice", document_name=doc.name)
                frappe.log_error(
                    title="eTIMS Purchase Invoice Submission Error",
                    message=f"Invoice: {doc.name}, Supplier: {doc.supplier}, Error: {str(e)}"
                )
                frappe.msgprint(
                    f"❌ Purchase invoice submission encountered an error. Check Error Log.",
                    indicator="red"
                )
        
        # Enqueue async processing to avoid blocking UI
        frappe.enqueue(
            _submit_purchase_transaction,
            is_async=True,
            queue="default",
            timeout=300,
            job_name=f"{doc.name}_send_purchase_information",
        )


def validate_item_registration(items) -> None:
    """Validate all items in the document are registered in eTIMS"""
    unregistered_items = []
    
    for item in items:
        item_doc = frappe.get_doc("Item", item.item_code)
        if not item_doc.custom_item_registered:
            unregistered_items.append(item.item_code)
    
    if unregistered_items:
        frappe.throw(
            f"""The following items are not registered in eTIMS:<br><br>
            <b>{', '.join(unregistered_items)}</b><br><br>
            Please register all items before submitting this document.""",
            title="Item Registration Required"
        )

def build_purchase_invoice_payload(doc: Document) -> dict:
	series_no = extract_document_series_number(doc)
	items_list = get_items_details(doc)
	taxation_type=get_taxation_types(doc)

	payload = {
		"invcNo": series_no,
		"orgInvcNo": 0,
		"spplrTin": doc.tax_id,
		"spplrBhfId": doc.custom_supplier_branch_id,
		"spplrNm": doc.supplier,
		"spplrInvcNo": doc.bill_no,
		"regTyCd": "A",
		"pchsTyCd": doc.custom_purchase_type_code,
		"rcptTyCd": doc.custom_receipt_type_code,
		"pmtTyCd": doc.custom_payment_type_code,
		"pchsSttsCd": doc.custom_purchase_status_code,
		"cfmDt": None,
		"pchsDt": "".join(str(doc.posting_date).split("-")),
		"wrhsDt": None,
		"cnclReqDt": "",
		"cnclDt": "",
		"rfdDt": None,
		"totItemCnt": len(items_list),
		
		"taxRtA": taxation_type.get("A", {}).get("tax_rate", 0),
		"taxRtB": taxation_type.get("B", {}).get("tax_rate", 0),
		"taxRtC": taxation_type.get("C", {}).get("tax_rate", 0),
		"taxRtD": taxation_type.get("D", {}).get("tax_rate", 0),
		"taxRtE": taxation_type.get("E", {}).get("tax_rate", 0),
		"taxAmtA": taxation_type.get("A", {}).get("tax_amount", 0),
		"taxAmtB": taxation_type.get("B", {}).get("tax_amount", 0),
		"taxAmtC": taxation_type.get("C", {}).get("tax_amount", 0),
		"taxAmtD": taxation_type.get("D", {}).get("tax_amount", 0),
		"taxAmtE": taxation_type.get("E", {}).get("tax_amount", 0),
		"taxblAmtA": taxation_type.get("A", {}).get("taxable_amount", 0),
		"taxblAmtB": taxation_type.get("B", {}).get("taxable_amount", 0),
		"taxblAmtC": taxation_type.get("C", {}).get("taxable_amount", 0),
		"taxblAmtD": taxation_type.get("D", {}).get("taxable_amount", 0),
		"taxblAmtE": taxation_type.get("E", {}).get("taxable_amount", 0),
		"totTaxblAmt": quantize_number(doc.base_net_total),
		"totTaxAmt": quantize_number(doc.total_taxes_and_charges),
		"totAmt": quantize_number(doc.grand_total),
		"remark": None,
		"regrNm": doc.owner,
		"regrId": split_user_email(doc.owner),
		"modrNm": doc.modified_by,
		"modrId": split_user_email(doc.modified_by),
		"itemList": items_list,
	}

	return payload


def get_items_details(doc: Document) -> list:
	items_list = []

	for index, item in enumerate(doc.items):

		items_list.append(
			{
				"itemSeq": item.idx,
				"itemCd": item.custom_item_code_etims,
				"itemClsCd": item.custom_item_classification_code,
				"itemNm": item.item_name,
				"bcd": "",
				"spplrItemClsCd": None,
				"spplrItemCd": None,
				"spplrItemNm": None,
				"pkgUnitCd": item.custom_packaging_unit_code,
				"pkg": 1,
				"qtyUnitCd": item.custom_unit_of_quantity_code,
				"qty": abs(item.qty),
				"prc": item.base_rate,
				"splyAmt": item.base_amount,
				"dcRt": quantize_number(item.discount_percentage) or 0,
				"dcAmt": quantize_number(item.discount_amount) or 0,
				"taxblAmt": quantize_number(item.net_amount),
				"taxTyCd": item.custom_taxation_type or "B",
				"taxAmt": quantize_number(item.custom_tax_amount) or 0,
				"totAmt": quantize_number(item.net_amount + item.custom_tax_amount),
				"itemExprDt": None,
			}
		)

	return items_list

def validate_item_registration(items):
	for item in items:
		item_code = item.item_code
		validation_message(item_code)
		
def validation_message(item_code):
	item_doc = frappe.get_doc("Item", item_code)
	
	if item_doc.custom_referenced_imported_item and (item_doc.custom_item_registered == 0 or item_doc.custom_imported_item_submitted == 0):
		item_link = get_link_to_form("Item", item_doc.name)
		frappe.throw(f"Register or submit the item: {item_link}")
	
	elif not item_doc.custom_referenced_imported_item and item_doc.custom_item_registered == 0:
		item_link = get_link_to_form("Item", item_doc.name)
		frappe.throw(f"Register the item: {item_link}")

