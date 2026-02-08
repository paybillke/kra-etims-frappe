const doctypeName = "eTims User";

frappe.ui.form.on(doctypeName, {
  refresh: async function (frm) {
    const companyName = frappe.boot.sysdefaults.company;

    if (!frm.is_new()) {
      frm.add_custom_button(
        __("Submit Branch User Details"),
        function () {
          frappe.call({
            method:
              "kra_etims_frappe.kra_etims.apis.apis.save_branch_user_details",
            args: {
              request_data: {
                name: frm.doc.name,
                company_name: companyName,
                user_id: frm.doc.system_user,
                full_names: frm.doc.users_full_names,
                branch_id: frm.doc.custom_etims_branch_id,
                registration_id: frm.doc.owner,
                modifier_id: frm.doc.modified_by,
              },
            },
            freeze: true,
            freeze_message: __("Sending request to eTIMS… Please wait"),          
            callback: (response) => {
              frappe.msgprint("Request queued. Please check in later.");
            },
            error: (r) => {
              // Error Handling is Defered to the Server
            },
          });
        },
        __("eTims Actions")
      );
    }
  },
});
