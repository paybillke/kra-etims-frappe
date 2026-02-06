from .kra_etims_frappe.doctype.doctype_names_mapping import (
    COUNTRIES_DOCTYPE_NAME,
    IMPORTED_ITEMS_STATUS_DOCTYPE_NAME,
    ITEM_CLASSIFICATIONS_DOCTYPE_NAME,
    PACKAGING_UNIT_DOCTYPE_NAME,
    PAYMENT_TYPE_DOCTYPE_NAME,
    PRODUCT_TYPE_DOCTYPE_NAME,
    PURCHASE_RECEIPT_DOCTYPE_NAME,
    STOCK_MOVEMENT_TYPE_DOCTYPE_NAME,
    TAXATION_TYPE_DOCTYPE_NAME,
    TRANSACTION_PROGRESS_DOCTYPE_NAME,
    TRANSACTION_TYPE_DOCTYPE_NAME,
    UNIT_OF_QUANTITY_DOCTYPE_NAME,
    ROUTES_URL_FUNCTION_DOCTYPE_NAME
)

app_name = "kra_etims_frappe"
app_title = "eTims Integration"
app_publisher = "Paybill Kenya"
app_description = (
    "A Frappe App for integrating with the Kenya Revenue Authority (KRA) Electronic Tax Invoice Management System (eTims) API"
)
app_email = "support@paybill.ke"
app_license = "MIT"
required_apps = ["frappe/erpnext"]


# Fixtures
# --------
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            [
                "dt",
                "in",
                (
                    "Item",
                    "Sales Invoice",
                    "Sales Invoice Item",
                    "Purchase Invoice",
                    "Purchase Invoice Item",
                    "Customer",
                    "Customer Group",
                    "Stock Ledger Entry",
                    "BOM",
                    "Warehouse",
                    "Item Tax Template",
                    "Branch",
                    "Supplier",
                ),
            ],
            ["is_system_generated", "=", 0],
        ],
    },
    {"dt": TRANSACTION_TYPE_DOCTYPE_NAME},
    {"dt": PURCHASE_RECEIPT_DOCTYPE_NAME},
    {"dt": UNIT_OF_QUANTITY_DOCTYPE_NAME},
    {"dt": IMPORTED_ITEMS_STATUS_DOCTYPE_NAME},
    {"dt": ROUTES_URL_FUNCTION_DOCTYPE_NAME},
    {"dt": COUNTRIES_DOCTYPE_NAME},
    {"dt": ITEM_CLASSIFICATIONS_DOCTYPE_NAME},
    {
        "dt": TAXATION_TYPE_DOCTYPE_NAME,
        "filters": [["name", "in", ("A", "B", "C", "D", "E")]],
    },
    {
        "dt": PRODUCT_TYPE_DOCTYPE_NAME,
        "filters": [["name", "in", (1, 2, 3)]],
    },
    {"dt": PACKAGING_UNIT_DOCTYPE_NAME},
    {"dt": STOCK_MOVEMENT_TYPE_DOCTYPE_NAME},
    {
        "dt": PAYMENT_TYPE_DOCTYPE_NAME,
        "filters": [
            [
                "name",
                "in",
                (
                    "CASH",
                    "CREDIT",
                    "CASH/CREDIT",
                    "BANK CHECK",
                    "DEBIT&CREDIT CARD",
                    "MOBILE MONEY",
                    "OTHER",
                ),
            ]
        ],
    },
    {
        "dt": TRANSACTION_PROGRESS_DOCTYPE_NAME,
        "filters": [
            [
                "name",
                "in",
                (
                    "Wait for Approval",
                    "Approved",
                    "Cancel Requested",
                    "Canceled",
                    "Credit Note Generated",
                    "Transferred",
                ),
            ]
        ],
    },
    {
        "doctype": "Workspace",
        "filters": [
            ["name", "=", "eTims Integration"],
        ],
    },
]
# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/kra_etims_frappe/css/kra_etims_frappe.css"
# app_include_js = "/assets/kra_etims_frappe/js/kra_etims_frappe.js"

# include js, css files in header of web template
# web_include_css = "/assets/kra_etims_frappe/css/kra_etims_frappe.css"
# web_include_js = "/assets/kra_etims_frappe/js/kra_etims_frappe.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "kra_etims_frappe/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
    "Sales Invoice": "kra_etims_frappe/overrides/client/sales_invoice.js",
    "Purchase Invoice": "kra_etims_frappe/overrides/client/purchase_invoice.js",
    "Customer": "kra_etims_frappe/overrides/client/customer.js",
    "Item": "kra_etims_frappe/overrides/client/items.js",
    "BOM": "kra_etims_frappe/overrides/client/bom.js",
    "Branch": "kra_etims_frappe/overrides/client/branch.js",
}

doctype_list_js = {
    "Item": "kra_etims_frappe/overrides/client/items_list.js",
    "Sales Invoice": "kra_etims_frappe/overrides/client/sales_invoice_list.js",
    "Branch": "kra_etims_frappe/overrides/client/branch_list.js",
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "kra_etims_frappe/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "kra_etims_frappe.utils.jinja_methods",
# 	"filters": "kra_etims_frappe.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "kra_etims_frappe.install.before_install"
# after_install = "kra_etims_frappe.kra_etims_frappe.setup.after_install.after_install"

# Uninstallation
# ------------

# before_uninstall = "kra_etims_frappe.uninstall.before_uninstall"
# after_uninstall = (
#     "kra_etims_frappe.kra_etims_frappe.setup.after_uninstall.after_uninstall"
# )

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "kra_etims_frappe.utils.before_app_install"
# after_app_install = "kra_etims_frappe.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "kra_etims_frappe.utils.before_app_uninstall"
# after_app_uninstall = "kra_etims_frappe.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "kra_etims_frappe.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    # 	"*": {
    # 		"on_update": "method",
    # 		"on_cancel": "method",
    # 		"on_trash": "method"
    # 	}
    "Sales Invoice": {
        "before_save":[
            "kra_etims_frappe.kra_etims_frappe.utils.before_save_"
        ],
        "on_submit": [
            "kra_etims_frappe.kra_etims_frappe.overrides.server.sales_invoice.on_submit"
        ],
        "validate": [
            "kra_etims_frappe.kra_etims_frappe.overrides.server.shared_overrides.validate"
        ],
        "before_cancel":[
            "kra_etims_frappe.kra_etims_frappe.overrides.server.sales_invoice.before_cancel"
        ],
    },
    "Purchase Invoice": {
        "before_save":[
            "kra_etims_frappe.kra_etims_frappe.utils.before_save_"
        ],
        "on_submit": [
            "kra_etims_frappe.kra_etims_frappe.overrides.server.purchase_invoice.on_submit"
        ],
        "validate": [
            "kra_etims_frappe.kra_etims_frappe.overrides.server.purchase_invoice.validate"
        ],
        "before_cancel":[
            "kra_etims_frappe.kra_etims_frappe.overrides.server.sales_invoice.before_cancel"
        ],
    },
    "Item": {
        "validate": [
            "kra_etims_frappe.kra_etims_frappe.overrides.server.item.validate"
            
        ],
        "on_trash": "kra_etims_frappe.kra_etims_frappe.overrides.server.item.prevent_item_deletion"
    },
}

# Scheduled Tasks
# ---------------

scheduler_events = {
    "all": [
        "kra_etims_frappe.kra_etims_frappe.background_tasks.tasks.send_stock_information",
        "kra_etims_frappe.kra_etims_frappe.background_tasks.tasks.send_item_inventory_information",
    ],
    # 	"daily": [
    # 		"kra_etims_frappe.tasks.daily"
    # 	],
    "hourly": [
        "kra_etims_frappe.kra_etims_frappe.background_tasks.tasks.send_sales_invoices_information",
        "kra_etims_frappe.kra_etims_frappe.background_tasks.tasks.send_purchase_information",
        "kra_etims_frappe.kra_etims_frappe.background_tasks.tasks.refresh_notices",
    ],
    # 	"weekly": [
    # 		"kra_etims_frappe.tasks.weekly"
    # 	],
    "monthly": [
        "kra_etims_frappe.kra_etims_frappe.background_tasks.tasks.refresh_code_lists",
        "kra_etims_frappe.kra_etims_frappe.background_tasks.tasks.get_item_classification_codes",
    ],
}

# Testing
# -------

# before_tests = "kra_etims_frappe.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "kra_etims_frappe.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "kra_etims_frappe.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["kra_etims_frappe.utils.before_request"]
# after_request = ["kra_etims_frappe.utils.after_request"]

# Job Events
# ----------
# before_job = ["kra_etims_frappe.utils.before_job"]
# after_job = ["kra_etims_frappe.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"kra_etims_frappe.auth.validate"
# ]
