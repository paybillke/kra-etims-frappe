import frappe

# import frappe
from frappe.tests.utils import FrappeTestCase

from ..doctype_names_mapping import TAXATION_TYPE_DOCTYPE_NAME


class TesteTimsTaxationType(FrappeTestCase):
    """Test Cases"""

    def test_duplicates(self) -> None:
        with self.assertRaises(frappe.DuplicateEntryError):
            doc = frappe.new_doc(TAXATION_TYPE_DOCTYPE_NAME)
            doc.cd = "Z"
            doc.save()

            doc = frappe.new_doc(TAXATION_TYPE_DOCTYPE_NAME)
            doc.cd = "Z"
            doc.save()
