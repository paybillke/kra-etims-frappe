import frappe
from frappe.model.document import Document

from .... import __version__
from frappe import _


def validate(doc: Document, method: str) -> None:
    
    new_prefix = f"{doc.custom_etims_country_of_origin_code}{doc.custom_product_type}{doc.custom_packaging_unit_code}{doc.custom_unit_of_quantity_code}"
    
    # Check if custom_item_code_etims exists and extract its suffix if so
    if doc.custom_item_code_etims:
        # Extract the last 7 digits as the suffix
        existing_suffix = doc.custom_item_code_etims[-7:]
    else:
        # If there is no existing code, generate a new suffix
        last_code = frappe.db.sql(
            """
            SELECT custom_item_code_etims 
            FROM `tabItem`
            WHERE custom_item_classification = %s
            ORDER BY CAST(SUBSTRING(custom_item_code_etims, -7) AS UNSIGNED) DESC
            LIMIT 1
            """,
            (doc.custom_item_classification,),
            as_dict=True,
        )

        if last_code:
            last_suffix = int(last_code[0]["custom_item_code_etims"][-7:])
            existing_suffix = str(last_suffix + 1).zfill(7)
        else:
            # Start from '0000001' if no matching classification item exists
            existing_suffix = "0000001"

    doc.custom_item_code_etims = f"{new_prefix}{existing_suffix}"

    # Check if the tax type field has changed
    is_tax_type_changed = doc.has_value_changed("custom_taxation_type")
    if doc.custom_taxation_type and is_tax_type_changed:
        relevant_tax_templates = frappe.get_all(
            "Item Tax Template",
            ["*"],
            {"custom_etims_taxation_type": doc.custom_taxation_type},
        )

        if relevant_tax_templates:
            doc.set("taxes", [])
            for template in relevant_tax_templates:
                doc.append("taxes", {"item_tax_template": template.name})

@frappe.whitelist()
def prevent_item_deletion(doc, method):
    if doc.custom_item_registered == 1:
        frappe.throw(_("Cannot delete registered items"))