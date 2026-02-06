"""Maps doctype names defined and used in the app to variable names"""

from typing import Final

# Doctypes
SETTINGS_DOCTYPE_NAME: Final[str] = "eTims Settings"
ITEM_CLASSIFICATIONS_DOCTYPE_NAME: Final[str] = "eTims Item Classification"
TAXATION_TYPE_DOCTYPE_NAME: Final[str] = "eTims Taxation Type"
PAYMENT_TYPE_DOCTYPE_NAME: Final[str] = "eTims Payment Type"
TRANSACTION_PROGRESS_DOCTYPE_NAME: Final[str] = "eTims Transaction Progress"
PACKAGING_UNIT_DOCTYPE_NAME: Final[str] = "eTims Packaging Unit"
UNIT_OF_QUANTITY_DOCTYPE_NAME: Final[str] = "eTims Unit of Quantity"
ENVIRONMENT_SPECIFICATION_DOCTYPE_NAME: Final[str] = (
    "eTims Environment Identifier"
)
INTEGRATION_LOGS_DOCTYPE_NAME: Final[str] = "eTims Integration Log"
STOCK_MOVEMENT_TYPE_DOCTYPE_NAME: Final[str] = "eTims Stock Movement Type"
PRODUCT_TYPE_DOCTYPE_NAME: Final[str] = "eTims Product Type"
COUNTRIES_DOCTYPE_NAME: Final[str] = "eTims Country"
IMPORTED_ITEMS_STATUS_DOCTYPE_NAME: Final[str] = "eTims Import Item Status"
PURCHASE_RECEIPT_DOCTYPE_NAME: Final[str] = "eTims Purchase Receipt Type"
TRANSACTION_TYPE_DOCTYPE_NAME: Final[str] = "eTims Transaction Type"
REGISTERED_PURCHASES_DOCTYPE_NAME: Final[str] = "eTims Registered Purchases"
REGISTERED_PURCHASES_DOCTYPE_NAME_ITEM: Final[str] = (
    "eTims Registered Purchases Items"
)
NOTICES_DOCTYPE_NAME: Final[str] = "eTims Notices"
USER_DOCTYPE_NAME: Final[str] = "eTims User"
REGISTERED_STOCK_MOVEMENTS_DOCTYPE_NAME: Final[str] = (
    "eTims Registered Stock Movement"
)
REGISTERED_STOCK_MOVEMENTS_ITEM_DOCTYPE_NAME: Final[str] = (
    "eTims Registered Stock Movement Item"
)
REGISTERED_IMPORTED_ITEM_DOCTYPE_NAME: Final[str] = (
    "eTims Registered Imported Item"
)

ROUTES_URL_FUNCTION_DOCTYPE_NAME: Final[str]  = ("eTims URL Path Function")

# Global Variables
SANDBOX_SERVER_URL: Final[str] = "https://etims-api-sbx.kra.go.ke/etims-api"
PRODUCTION_SERVER_URL: Final[str] = "https://etims-api.kra.go.ke/etims-api"
