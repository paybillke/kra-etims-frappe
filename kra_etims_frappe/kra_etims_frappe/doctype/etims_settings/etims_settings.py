"""eTIMS Integration Settings with SDK-based OSCU initialization"""
import frappe
from frappe.model.document import Document
from frappe.integrations.utils import create_request_log

from kra_etims_sdk.auth import AuthClient
from kra_etims_sdk.client import EtimsClient
from kra_etims_sdk.exceptions import ApiException, AuthenticationException

# Local imports
from ...doctype.doctype_names_mapping import (
    SETTINGS_DOCTYPE_NAME,
)
from ...utils import (
    is_valid_kra_pin,
    update_last_request_date,
    update_integration_request,
)
from ...logger import etims_logger
from ...handlers import handle_errors


class eTimsSettings(Document):
    """eTIMS Integration Settings doctype with SDK-based initialization"""

    def validate(self) -> None:
        """Validation Hook - ensures data integrity before save"""
        self.error_title = "Validation Error"
        self.sandboxServerUrl = 'https://etims-api-sbx.kra.go.ke/etims-api'
        self.productionServerUrl = 'https://etims-api.kra.go.ke/etims-api'

        # Set environment flag and server URL based on sandbox toggle
        self.env = "Sandbox" if self.sandbox else "Production"
        self.server_url = self.sandboxServerUrl if self.sandbox else self.productionServerUrl

        # Validate branch ID format (exactly 2 characters per KRA spec)
        if self.bhfid and len(self.bhfid) != 2:
            frappe.throw(
                "Branch ID must be exactly 2 characters (e.g., '00', '01')",
                frappe.ValidationError,
                title=self.error_title
            )

        # Validate device serial number length (max 100 chars per KRA spec)
        if self.dvcsrlno and len(self.dvcsrlno) > 100:
            frappe.throw(
                "Device Serial Number cannot exceed 100 characters",
                frappe.ValidationError,
                title=self.error_title
            )

        if not self.consumer_key:
            frappe.throw(
                "Consumer Key is mandatory for eTIMS integration",
                frappe.ValidationError,
                title=self.error_title
            )

        if not self.consumer_secret:
            frappe.throw(
                "Consumer Secret is mandatory for eTIMS integration",
                frappe.ValidationError,
                title=self.error_title
            )

        # Mandatory company field
        if not self.company:
            frappe.throw(
                "Company is mandatory for eTIMS integration",
                frappe.ValidationError,
                title=self.error_title
            )

        # Mandatory PIN validation
        if not self.tin:
            frappe.throw(
                "Taxpayer PIN (TIN) is mandatory for eTIMS integration",
                frappe.ValidationError,
                title=self.error_title
            )

        # Validate PIN format (KRA format: PXXXXXXXXX)
        if self.tin and not is_valid_kra_pin(self.tin):
            frappe.throw(
                "The Taxpayer PIN you entered does not resemble a valid KRA PIN format (should start with 'P' followed by 9 digits)",
                frappe.ValidationError,
                title=self.error_title
            )

        # Ensure mutual exclusivity of active settings for same company/env/branch
        # Using parameterized query to prevent SQL injection
        if self.is_active and not self.get("__islocal"):
            frappe.db.sql("""
                UPDATE `tabKRA eTims Settings`
                SET is_active = 0
                WHERE name != %s
                    AND company = %s
                    AND env = %s
                    AND bhfid = %s
                    AND is_active = 1
            """, (self.name, self.company, self.env, self.bhfid))

    def before_insert(self) -> None:
        """
        Before Insert Hook - performs OSCU initialization with KRA servers
        using SDK instead of custom HTTP requests
        """
        # Skip initialization for sandbox environments during development/testing
        if frappe.conf.developer_mode and self.sandbox:
            frappe.msgprint(
                "⚠️ Skipping OSCU initialization in developer mode (sandbox environment)",
                indicator="orange"
            )
            return

        # Build minimal config for OSCU initialization
        env_key = 'sbx' if self.sandbox else 'prod'
        config = {
            'env': env_key,
            'auth': {
                'sbx': {
                    'token_url': 'https://sbx.kra.go.ke/v1/token/generate',
                    'consumer_key': self.consumer_key,
                    'consumer_secret': self.consumer_secret,
                },
                'prod': {
                    'token_url': 'https://kra.go.ke/v1/token/generate',
                    'consumer_key': self.consumer_key,
                    'consumer_secret': self.consumer_secret,
                }
            },
            'api': {
                'sbx': {'base_url': 'https://etims-api-sbx.kra.go.ke/etims-api'},
                'prod': {'base_url': 'https://etims-api.kra.go.ke/etims-api'}
            },
            'http': {'timeout': 30},
            'oscu': {
                'tin': self.tin,
                'bhf_id': self.bhfid,
                'device_serial': self.dvcsrlno,
                'cmc_key': '',  # Will be populated by initialization response
            }
        }

        # Create integration request for audit trail BEFORE API call
        integration_request = create_request_log(
            data={
                "tin": self.tin,
                "bhfId": self.bhfid,
                "dvcSrlNo": self.dvcsrlno,
                "env": "Sandbox" if self.sandbox else "Production"
            },
            service_name="eTIMS",
            url=f"SDK:{env_key}:init_oscu",
            request_headers=None,
            is_remote_request=True,
            reference_doctype=SETTINGS_DOCTYPE_NAME,
            reference_docname=self.name,
        )

        try:
            # Initialize SDK clients
            auth = AuthClient(config)
            client = EtimsClient(config, auth)

            # Perform OSCU initialization (replaces DeviceVerificationReq)
            # SDK method name may vary - verify with your SDK version
            response = client.select_init_osdc_info({
                'tin': self.tin,
                'bhfId': self.bhfid,
                'dvcSrlNo': self.dvcsrlno,
            })

            if response.get("resultCd") == "000":
                # Extract critical OSCU parameters from response
                data = response.get("data", {})
                info = data.get("info", {})
                
                self.communication_key = info.get("cmcKey", "")
                self.sales_control_unit_id = info.get("sdcId", "")
                
                # Update last request date for route tracking
                if "resultDt" in response:
                    update_last_request_date(response["resultDt"], "/DeviceVerificationReq")
                
                # Update integration request status
                update_integration_request(
                    integration_request.name,
                    "Completed",
                    output=str(response),
                    error=None,
                )
                
                frappe.msgprint(
                    f"✅ OSCU initialized successfully<br>"
                    f"<b>SCU ID:</b> {self.sales_control_unit_id}<br>"
                    f"<b>Communication Key:</b> {self.communication_key[:10]}...",
                    indicator="green",
                    alert=True
                )
            else:
                # Handle KRA API errors with user-friendly messages
                error_msg = response.get("resultMsg", "Unknown error")
                error_code = response.get("resultCd", "N/A")
                
                update_integration_request(
                    integration_request.name,
                    "Failed",
                    output=str(response),
                    error=f"{error_msg} (Code: {error_code})",
                )
                
                frappe.log_error(
                    title="eTIMS OSCU Initialization Failed",
                    message=f"Settings: {self.name}, PIN: {self.tin}, Branch: {self.bhfid}, Error: {error_msg}, Code: {error_code}"
                )
                
                frappe.throw(
                    f"OSCU initialization failed:<br><b>{error_msg}</b><br><small>Error Code: {error_code}</small>",
                    title="Initialization Failed"
                )

        except AuthenticationException as e:
            # Handle auth failures specifically (consumer key/secret issues)
            update_integration_request(
                integration_request.name,
                "Failed",
                output=None,
                error=f"Authentication failed: {str(e)}",
            )
            frappe.log_error(
                title="eTIMS Authentication Error",
                message=f"Settings: {self.name}, PIN: {self.tin}, Error: {str(e)}"
            )
            frappe.throw(
                f"Authentication failed. Verify your Consumer Key and Consumer Secret:<br>{str(e)}",
                title="Authentication Error"
            )

        except ApiException as e:
            # Handle SDK API exceptions (network, timeouts, KRA server errors)
            update_integration_request(
                integration_request.name,
                "Failed",
                output=None,
                error=f"API error: {str(e)}",
            )
            frappe.log_error(
                title="eTIMS API Error",
                message=f"Settings: {self.name}, PIN: {self.tin}, Error: {str(e)}"
            )
            frappe.throw(
                f"KRA API error during OSCU initialization:<br>{str(e)}",
                title="API Error"
            )

        except Exception as e:
            # Handle all other unexpected exceptions
            update_integration_request(
                integration_request.name,
                "Failed",
                output=None,
                error=f"Unexpected error: {str(e)}",
            )
            frappe.log_error(
                title="eTIMS Initialization Error",
                message=f"Settings: {self.name}, PIN: {self.tin}, Error: {str(e)}"
            )
            frappe.throw(
                f"Unexpected error during OSCU initialization:<br>{str(e)}",
                title="Initialization Error"
            )

    def on_update(self) -> None:
        """On Update Hook - manages background task scheduling and dimension creation"""
        # Ensure at least one active setting exists for this company/env/branch
        if not self.is_active:
            active_count = frappe.db.count(
                SETTINGS_DOCTYPE_NAME,
                filters={
                    "is_active": 1,
                    "company": self.company,
                    "env": self.env,
                    "bhfid": self.bhfid,
                }
            )
            
            if active_count == 0:
                # Auto-activate this record if it's the only one
                frappe.db.set_value(SETTINGS_DOCTYPE_NAME, self.name, "is_active", 1)
                frappe.db.commit()
                self.reload()

        # Update scheduled job frequencies
        self._update_scheduled_job_frequency(
            "send_sales_invoices_information",
            self.sales_information_submission,
            self.sales_info_cron_format if self.sales_information_submission == "Cron" else None
        )

        self._update_scheduled_job_frequency(
            "send_stock_information",
            self.stock_information_submission,
            self.stock_info_cron_format if self.stock_information_submission == "Cron" else None
        )
        
        self._update_scheduled_job_frequency(
            "send_item_inventory_information",
            self.stock_information_submission,
            self.stock_info_cron_format if self.stock_information_submission == "Cron" else None
        )

        self._update_scheduled_job_frequency(
            "send_purchase_information",
            self.purchase_information_submission,
            self.purchase_info_cron_format if self.purchase_information_submission == "Cron" else None
        )

        self._update_scheduled_job_frequency(
            "refresh_notices",
            self.notices_refresh_frequency,
            self.notices_refresh_freq_cron_format if self.notices_refresh_frequency == "Cron" else None
        )

        # Handle accounting dimension creation
        if self.autocreate_branch_dimension and self.is_active:
            self._create_branch_dimension()

    def _update_scheduled_job_frequency(self, method_name: str, frequency: str | None, cron_format: str | None = None) -> None:
        """Helper to update scheduled job frequency and cron format safely"""
        if not frequency:
            return

        try:
            # Find job by partial method name match
            job = frappe.get_doc("Scheduled Job Type", {"method": ["like", f"%{method_name}%"]})
            job.frequency = frequency
            
            if frequency == "Cron" and cron_format:
                job.cron_format = cron_format
            
            job.save(ignore_permissions=True)
            frappe.db.commit()
        except frappe.DoesNotExistError:
            frappe.log_error(
                title="Scheduled Job Not Found",
                message=f"Could not find scheduled job for method pattern: {method_name}"
            )
        except Exception as e:
            frappe.log_error(
                title="Scheduled Job Update Error",
                message=f"Failed to update job '{method_name}': {str(e)}"
            )

    def _create_branch_dimension(self) -> None:
        """Create Branch accounting dimension if it doesn't exist"""
        if frappe.db.exists("Accounting Dimension", {"document_type": "Branch"}):
            return

        company = frappe.defaults.get_user_default("Company")
        if not company:
            frappe.throw(
                "Default company not set in user defaults. Cannot create Branch dimension.",
                title="Configuration Error"
            )

        try:
            dimension = frappe.new_doc("Accounting Dimension")
            dimension.document_type = "Branch"
            dimension.append("dimension_defaults", {
                "company": company,
                "mandatory_for_pl": 1,
                "mandatory_for_bs": 1,
            })
            dimension.save(ignore_permissions=True)
            frappe.msgprint("✅ Branch accounting dimension created successfully", indicator="green", alert=True)
        except Exception as e:
            frappe.log_error(
                title="Branch Dimension Creation Error",
                message=f"Failed to create Branch dimension: {str(e)}"
            )
            frappe.throw(
                f"Failed to create Branch accounting dimension:<br>{str(e)}",
                title="Dimension Creation Error"
            )