from functools import partial
from hashlib import sha256
from typing import Literal
import json

import frappe
from frappe.model.document import Document
from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data
from frappe.integrations.utils import create_request_log

from ...apis.remote_response_status_handlers import (
    on_error,
    stock_mvt_submission_on_success,
)
from ...utils import (
    extract_document_series_number,
    split_user_email,
    update_last_request_date,
    quantize_number,
)

from ...apis.apis import  (
    EtimsSDKWrapper,
    update_integration_request_status
)

def on_update(doc: Document, method: str | None = None) -> None:
    """
    Hook triggered on Stock Ledger Entry update to submit stock movements to eTIMS.
    Uses SDK for reliable, authenticated communication with KRA servers.
    """
    # Skip processing if document is cancelled or has negative quantity (KRA requirement)
    if doc.docstatus == 2 or doc.actual_qty <= 0:
        return

    # Skip Sales Invoices not yet successfully submitted to eTIMS
    if doc.voucher_type == "Sales Invoice":
        record = frappe.get_doc(doc.voucher_type, doc.voucher_no)
        if record.custom_successfully_submitted != 1:
            return

    company_name = doc.company
    vendor = "OSCU KRA"
    
    def _submit_stock_movement():
        try:
            # Get branch ID from warehouse (critical for multi-branch setups)
            branch_id = get_warehouse_branch_id(doc.warehouse) or "00"
            client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
            
            # Create integration request BEFORE API call for audit trail
            integration_request = create_request_log(
                data={"voucher_type": doc.voucher_type, "voucher_no": doc.voucher_no},
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:save_stock_movement",
                reference_docname=doc.name,
                reference_doctype="Stock Ledger Entry",
            )
            
            # Build payload with preserved business logic
            payload = _build_stock_movement_payload(doc, company_name, vendor)
            
            # Submit to eTIMS via SDK
            response = client.save_stock_master(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                stock_mvt_submission_on_success(response, document_name=doc.name)
                
                # Update last request date for incremental syncs
                if "resultDt" in response:
                    update_last_request_date(response["resultDt"], "/StockIOSaveReq")
            else:
                update_integration_request_status(
                    integration_request.name,
                    status="Failed",
                    output=None,
                    error=response.get("resultMsg", "Unknown error")
                )
                on_error(
                    response.get("resultMsg", "Unknown error"),
                    url="/StockIOSaveReq",
                    doctype="Stock Ledger Entry",
                    document_name=doc.name,
                )
                frappe.log_error(
                    title="eTIMS Stock Movement Submission Failed",
                    message=f"Doc: {doc.name}, Voucher: {doc.voucher_no}, Error: {response.get('resultMsg', 'Unknown')}"
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
            on_error(str(e), url="/StockIOSaveReq", doctype="Stock Ledger Entry", document_name=doc.name)
            frappe.log_error(
                title="eTIMS Stock Movement Error",
                message=f"Doc: {doc.name}, Voucher: {doc.voucher_no}, Error: {str(e)}"
            )

    # Generate deterministic job name (safe length)
    job_name = hashlib.sha256(
        f"etims_stock_mvt_{doc.name}_{doc.modified}".encode()
    ).hexdigest()[:32]

    # Enqueue async processing to avoid blocking UI
    frappe.enqueue(
        _submit_stock_movement,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=job_name,
    )


def _build_stock_movement_payload(doc: Document, company_name: str, vendor: str) -> dict:
    """Build stock movement payload preserving all business logic from original implementation"""
    record = frappe.get_doc(doc.voucher_type, doc.voucher_no)
    series_no = extract_document_series_number(record)
    
    # Base payload structure
    payload = {
        "sarNo": series_no,
        "orgSarNo": series_no,
        "regTyCd": "M",  # Manual registration type
        "custTin": None,
        "custNm": None,
        "custBhfId": get_warehouse_branch_id(doc.warehouse) or None,
        "ocrnDt": record.posting_date.strftime("%Y%m%d"),
        "totTaxblAmt": 0,
        "totItemCnt": len(record.items),
        "totTaxAmt": 0,
        "totAmt": 0,
        "remark": None,
        "regrId": split_user_email(record.owner),
        "regrNm": record.owner,
        "modrNm": record.modified_by,
        "modrId": split_user_email(record.modified_by),
    }
    
    # Get all items for metadata lookup (optimized query)
    all_items = frappe.db.get_all(
        "Item",
        fields=["name", "item_code", "custom_item_code_etims", "custom_item_classification", 
                "custom_packaging_unit_code", "custom_unit_of_quantity_code", 
                "custom_taxation_type", "is_stock_item"]
    )
    
    # Voucher-type specific logic
    if doc.voucher_type == "Stock Reconciliation":
        items_list = get_stock_recon_movement_items_details(record.items, all_items)
        current_item = [item for item in items_list if item["itemNm"] == doc.item_code]
        
        if not current_item:
            frappe.throw(f"Item {doc.item_code} not found in reconciliation items", title="Data Error")
        
        qty_diff = int(current_item[0].pop("quantity_difference", 0))
        payload["itemList"] = current_item
        payload["totItemCnt"] = len(current_item)
        
        if record.purpose == "Opening Stock":
            payload["sarTyCd"] = "06"  # Opening stock
        elif qty_diff < 0:
            payload["sarTyCd"] = "16"  # Stock decrease
        else:
            payload["sarTyCd"] = "06"  # Stock increase

    elif doc.voucher_type == "Stock Entry":
        items_list = get_stock_entry_movement_items_details(record.items, all_items)
        current_item = [item for item in items_list if item["itemNm"] == doc.item_code]
        
        if not current_item:
            frappe.throw(f"Item {doc.item_code} not found in stock entry items", title="Data Error")
        
        payload["itemList"] = current_item
        payload["totItemCnt"] = len(current_item)
        
        # Map stock entry types to eTIMS movement codes
        entry_type_map = {
            "Material Receipt": "04",
            "Manufacture": "05" if doc.actual_qty > 0 else "14",
            "Material Transfer": None,  # Requires special handling below
            "Send to Subcontractor": "13",
            "Material Issue": "13",
            "Repack": "05" if doc.actual_qty > 0 else "14",
        }
        
        if record.stock_entry_type == "Material Transfer":
            voucher_details = frappe.db.get_value(
                "Stock Entry Detail",
                {"name": doc.voucher_detail_no},
                ["s_warehouse", "t_warehouse"],
                as_dict=True,
            )
            
            if doc.actual_qty < 0:  # Source warehouse movement
                payload["custBhfId"] = get_warehouse_branch_id(voucher_details.t_warehouse)
                payload["sarTyCd"] = "13"
            else:  # Target warehouse movement
                payload["custBhfId"] = get_warehouse_branch_id(voucher_details.s_warehouse)
                payload["sarTyCd"] = "04"
        else:
            payload["sarTyCd"] = entry_type_map.get(record.stock_entry_type, "04")

    elif doc.voucher_type in ("Purchase Receipt", "Purchase Invoice"):
        items_list = get_purchase_docs_items_details(record.items, all_items)
        item_taxes = get_itemised_tax_breakup_data(record)
        
        current_item = [item for item in items_list if item["itemNm"] == doc.item_code]
        if not current_item:
            frappe.throw(f"Item {doc.item_code} not found in purchase items", title="Data Error")
        
        # Tax details lookup (preserving commented logic for future use)
        tax_details = next((i for i in item_taxes if i["item"] == doc.item_code), None)
        # if tax_details:
        #     current_item[0]["taxblAmt"] = round(tax_details["taxable_amount"] / current_item[0]["qty"], 2)
        #     current_item[0]["totAmt"] = round(tax_details["taxable_amount"] / current_item[0]["qty"], 2)
        
        payload["itemList"] = current_item
        payload["totItemCnt"] = len(current_item)
        payload["sarTyCd"] = "12" if record.is_return else ("01" if current_item[0].get("is_imported_item") else "02")

    elif doc.voucher_type in ("Delivery Note", "Sales Invoice"):
        items_list = get_notes_docs_items_details(record.items, all_items)
        item_taxes = get_itemised_tax_breakup_data(record)
        
        current_item = [item for item in items_list if item["itemNm"] == doc.item_code]
        if not current_item:
            frappe.throw(f"Item {doc.item_code} not found in sales items", title="Data Error")
        
        tax_details = next((i for i in item_taxes if i["item"] == doc.item_code), None)
        
        payload["itemList"] = current_item
        payload["totItemCnt"] = len(current_item)
        payload["custNm"] = record.customer
        payload["custTin"] = record.tax_id
        
        # Sales return logic
        if record.is_return:
            payload["sarTyCd"] = "03" if doc.actual_qty > 0 else "11"
        else:
            payload["sarTyCd"] = "11"  # Normal sales

    return payload

def get_stock_entry_movement_items_details(
    records: list[Document], all_items: list[Document]
) -> list[dict]:
    items_list = []

    for item in records:
        for fetched_item in all_items:
            if item.item_code == fetched_item.name:
                items_list.append(
                    {
                        "itemSeq": item.idx,
                        "itemCd": fetched_item.custom_item_code_etims,
                        "itemClsCd": fetched_item.custom_item_classification,
                        "itemNm": fetched_item.item_code,
                        "bcd": None,
                        "pkgUnitCd": fetched_item.custom_packaging_unit_code,
                        "pkg": 1,
                        "qtyUnitCd": fetched_item.custom_unit_of_quantity_code,
                        "qty": abs(item.qty),
                        "itemExprDt": "",
                        "prc": (
                            round(int(item.basic_rate), 2) if item.basic_rate else 0
                        ),
                        "splyAmt": (
                            round(int(item.basic_rate), 2) if item.basic_rate else 0
                        ),
                        # TODO: Handle discounts properly
                        "totDcAmt": 0,
                        "taxTyCd": fetched_item.custom_taxation_type_code or "B",
                        "taxblAmt": 0,
                        "taxAmt": 0,
                        "totAmt": 0,
                    }
                )

    return items_list


def get_stock_recon_movement_items_details(
    records: list, all_items: list
) -> list[dict]:
    items_list = []
    # current_qty

    for item in records:
        for fetched_item in all_items:
            if item.item_code == fetched_item.name:
                items_list.append(
                    {
                        "itemSeq": item.idx,
                        "itemCd": fetched_item.custom_item_code_etims,
                        "itemClsCd": fetched_item.custom_item_classification,
                        "itemNm": fetched_item.item_code,
                        "bcd": None,
                        "pkgUnitCd": fetched_item.custom_packaging_unit_code,
                        "pkg": 1,
                        "qtyUnitCd": fetched_item.custom_unit_of_quantity_code,
                        "qty": abs(int(item.quantity_difference)),
                        "itemExprDt": "",
                        "prc": (
                            round(int(item.valuation_rate), 2)
                            if item.valuation_rate
                            else 0
                        ),
                        "splyAmt": (
                            round(int(item.valuation_rate), 2)
                            if item.valuation_rate
                            else 0
                        ),
                        "totDcAmt": 0,
                        "taxTyCd": fetched_item.custom_taxation_type_code or "B",
                        "taxblAmt": 0,
                        "taxAmt": 0,
                        "totAmt": 0,
                        "quantity_difference": item.quantity_difference,
                    }
                )

    return items_list


def get_purchase_docs_items_details(
    items: list, all_present_items: list[Document]
) -> list[dict]:
    items_list = []

    for item in items:
        for fetched_item in all_present_items:
            if item.item_code == fetched_item.name:
                items_list.append(
                    {
                        "itemSeq": item.idx,
                        "itemCd": fetched_item.custom_item_code_etims,
                        "itemClsCd": fetched_item.custom_item_classification,
                        "itemNm": fetched_item.item_code,
                        "bcd": None,
                        "pkgUnitCd": fetched_item.custom_packaging_unit_code,
                        "pkg": 1,
                        "qtyUnitCd": fetched_item.custom_unit_of_quantity_code,
                        "qty": abs(item.qty),
                        "itemExprDt": "",
                        "prc": (
                            round(int(item.valuation_rate), 2)
                            if item.valuation_rate
                            else 0
                        ),
                        "splyAmt": (
                            round(int(item.valuation_rate), 2)
                            if item.valuation_rate
                            else 0
                        ),
                        "totDcAmt": 0,
                        "taxTyCd": fetched_item.custom_taxation_type_code or "B",
                        "taxblAmt": quantize_number(item.net_amount),
                        "taxAmt": quantize_number(item.custom_tax_amount) or 0,
                        "totAmt": quantize_number(item.net_amount + item.custom_tax_amount),
                        "is_imported_item": (
                            True
                            if (
                                fetched_item.custom_imported_item_status
                                and fetched_item.custom_imported_item_task_code
                            )
                            else False
                        ),
                    }
                )

    return items_list


def get_notes_docs_items_details(
    items: list[Document], all_present_items: list[Document]
) -> list[dict]:
    items_list = []

    for item in items:
        for fetched_item in all_present_items:
            if item.item_code == fetched_item.name:
                items_list.append(
                    {
                        "itemSeq": item.idx,
                        "itemCd": None,
                        "itemClsCd": fetched_item.custom_item_classification,
                        "itemNm": fetched_item.item_code,
                        "bcd": None,
                        "pkgUnitCd": fetched_item.custom_packaging_unit_code,
                        "pkg": 1,
                        "qtyUnitCd": fetched_item.custom_unit_of_quantity_code,
                        "qty": abs(item.qty),
                        "itemExprDt": "",
                        "prc": (
                            round(int(item.base_net_rate), 2)
                            if item.base_net_rate
                            else 0
                        ),
                        "splyAmt": (
                            round(int(item.base_net_rate), 2)
                            if item.base_net_rate
                            else 0
                        ),
                        "totDcAmt": 0,
                        "taxTyCd": fetched_item.custom_taxation_type_code or "B",
                        "taxblAmt": quantize_number(item.net_amount),
                        "taxAmt": quantize_number(item.custom_tax_amount) or 0,
                        "totAmt": quantize_number(item.net_amount + item.custom_tax_amount),
                    }
                )

    return items_list


def get_warehouse_branch_id(warehouse_name: str) -> str | Literal[0]:
    branch_id = frappe.db.get_value(
        "Warehouse", {"name": warehouse_name}, ["custom_branch"], as_dict=True
    )

    if branch_id:
        return branch_id.custom_branch

    return 0
