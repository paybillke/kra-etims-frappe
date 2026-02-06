from collections import defaultdict
from functools import partial
from typing import Literal
import json
import frappe
from frappe.model.document import Document
from frappe.integrations.utils import create_request_log

from ...utils import (
    build_invoice_payload,
    get_curr_env_etims_settings,
    update_last_request_date,
)
from ...apis.remote_response_status_handlers import (
    on_error,
    sales_information_submission_on_success,
)

from ...apis.apis import  (
    EtimsSDKWrapper,
    update_integration_request_status
)

def generic_invoices_on_submit_override(
    doc: Document, invoice_type: Literal["Sales Invoice", "POS Invoice"]
) -> None:
    """Submit sales transaction to eTIMS using SDK when invoice is submitted"""
    company_name = doc.company
    vendor = "OSCU KRA"
    branch_id = doc.branch or "00"
    
    def _submit_sales_transaction():
        try:
            # Get SDK client with branch-specific configuration
            client = EtimsSDKWrapper.get_client(company_name, vendor, branch_id)
            
            # Create integration request BEFORE API call for audit trail
            integration_request = create_request_log(
                data={"invoice_type": invoice_type, "invoice_no": doc.name},
                is_remote_request=True,
                service_name="eTIMS",
                request_headers={},
                url=f"SDK:{client.config['env']}:save_transaction_sales_osdc",
                reference_docname=doc.name,
                reference_doctype=invoice_type,
            )
            
            # Build payload using existing helper function
            invoice_identifier = "C" if doc.is_return else "S"
            payload = build_invoice_payload(doc, invoice_identifier, company_name)
            
            # Submit to eTIMS via SDK
            response = client.save_transaction_sales_osdc(payload)
            
            if response.get("resultCd") == "000":
                update_integration_request_status(
                    integration_request.name,
                    status="Completed",
                    output=json.dumps(response),
                    error=None
                )
                
                # Call success handler with all required context
                sales_information_submission_on_success(
                    response,
                    document_name=doc.name,
                    invoice_type=invoice_type,
                    company_name=company_name,
                    invoice_number=payload["invcNo"],
                    pin=client.config["oscu"]["tin"],
                    branch_id=client.config["oscu"]["bhf_id"],
                )
                
                # Update last request date for incremental syncs
                if "resultDt" in response:
                    update_last_request_date(response["resultDt"], "/TrnsSalesSaveWrReq")
                
                frappe.msgprint(
                    f"✅ {invoice_type} {doc.name} submitted successfully to eTIMS",
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
                    url="/TrnsSalesSaveWrReq",
                    doctype=invoice_type,
                    document_name=doc.name,
                )
                frappe.log_error(
                    title=f"eTIMS {invoice_type} Submission Failed",
                    message=f"Invoice: {doc.name}, Error: {response.get('resultMsg', 'Unknown')}"
                )
                frappe.msgprint(
                    f"❌ {invoice_type} submission failed. Check Error Log.",
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
            on_error(str(e), url="/TrnsSalesSaveWrReq", doctype=invoice_type, document_name=doc.name)
            frappe.log_error(
                title=f"eTIMS {invoice_type} Submission Error",
                message=f"Invoice: {doc.name}, Error: {str(e)}"
            )
            frappe.msgprint(
                f"❌ {invoice_type} submission encountered an error. Check Error Log.",
                indicator="red"
            )

    # Enqueue async processing to avoid blocking UI
    frappe.enqueue(
        _submit_sales_transaction,
        is_async=True,
        queue="default",
        timeout=300,
        job_name=f"{doc.name}_send_sales_request",
    )


def validate(doc: Document, method: str | None = None) -> None:
    """Validation hook for sales invoices - sets SCU ID and validates branch"""
    vendor = "OSCU KRA"
    
    # Get company from document or fallback to user default
    company_name = doc.company or frappe.defaults.get_user_default("Company")
    
    if not company_name:
        frappe.throw("Company is not set. Please ensure a company is selected.", title="Validation Error")
    
    # Validate branch is set
    if not doc.branch:
        frappe.throw("Please ensure the branch is set before saving the document", title="Validation Error")
    
    try:
        # Get SCU ID from eTIMS settings
        settings = get_curr_env_etims_settings(company_name, vendor, doc.branch)
        
        if settings and settings.scu_id:
            doc.custom_scu_id = settings.scu_id
        else:
            frappe.log_error(
                title="eTIMS SCU ID Missing",
                message=f"No SCU ID found for company: {company_name}, branch: {doc.branch}"
            )
            # Don't throw error here - allow document save but log warning
            frappe.msgprint(
                "⚠️ Warning: No SCU ID found in eTIMS settings. Please configure eTIMS settings.",
                indicator="orange"
            )
            
    except Exception as e:
        frappe.log_error(
            title="eTIMS Settings Validation Error",
            message=f"Company: {company_name}, Branch: {doc.branch}, Error: {str(e)}"
        )
        # Don't block document save on settings error - log and warn
        frappe.msgprint(
            f"⚠️ Warning: Could not load eTIMS settings. Error: {str(e)}",
            indicator="orange"
        )
    
    # Tax breakdown calculation (commented out in original - preserved for future use)
    # item_taxes = get_itemised_tax_breakup_data(doc)
    # taxes_breakdown = defaultdict(list)
    # taxable_breakdown = defaultdict(list)
    # if doc.taxes:
    #     tax_head = doc.taxes[0].description
    #     for index, item in enumerate(doc.items):
    #         if index < len(item_taxes):
    #             taxes_breakdown[item.custom_taxation_type_code].append(
    #                 item_taxes[index].get(tax_head, {}).get("tax_amount", 0)
    #             )
    #             taxable_breakdown[item.custom_taxation_type_code].append(
    #                 item_taxes[index].get("taxable_amount", 0)
    #             )
    #     update_tax_breakdowns(doc, (taxes_breakdown, taxable_breakdown))


# def update_tax_breakdowns(invoice: Document, mapping: tuple) -> None:
#     """Update custom tax fields on invoice (preserved from original for future use)"""
#     if mapping and len(mapping) >= 2:
#         taxes_breakdown, taxable_breakdown = mapping
#         
#         invoice.custom_tax_a = round(sum(taxes_breakdown.get("A", [])), 2)
#         invoice.custom_tax_b = round(sum(taxes_breakdown.get("B", [])), 2)
#         invoice.custom_tax_c = round(sum(taxes_breakdown.get("C", [])), 2)
#         invoice.custom_tax_d = round(sum(taxes_breakdown.get("D", [])), 2)
#         invoice.custom_tax_e = round(sum(taxes_breakdown.get("E", [])), 2)
# 
#         invoice.custom_taxbl_amount_a = round(sum(taxable_breakdown.get("A", [])), 2)
#         invoice.custom_taxbl_amount_b = round(sum(taxable_breakdown.get("B", [])), 2)
#         invoice.custom_taxbl_amount_c = round(sum(taxable_breakdown.get("C", [])), 2)
#         invoice.custom_taxbl_amount_d = round(sum(taxable_breakdown.get("D", [])), 2)
#         invoice.custom_taxbl_amount_e = round(sum(taxable_breakdown.get("E", [])), 2)