import frappe
from frappe.model.document import Document

from .logger import etims_logger

def handle_errors(
    response: dict[str, str],
    route: str,
    document_name: str,
    doctype: str | Document | None = None,
    integration_request_name: str | None = None,
) -> None:
    
    etims_logger.error("%s" % (response))

    try:
        frappe.throw(
            response,
            frappe.InvalidStatusError,
            title=f"Error",
        )

    except frappe.InvalidStatusError as error:
        frappe.log_error(
            frappe.get_traceback(with_context=True),
            error,
            reference_name=document_name,
            reference_doctype=doctype,
        )
        raise
