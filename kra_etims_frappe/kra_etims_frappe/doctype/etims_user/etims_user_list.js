const doctypeName = "eTims User";

frappe.listview_settings[doctypeName] = {
  onload: function (listview) {
    const companyName = frappe.boot.sysdefaults.company;

    listview.page.add_inner_button(
      __("Create from Current System Users"),
      function (listview) {
        frappe.call({
          method:
            "kra_etims_frappe.kra_etims_frappe.apis.apis.create_branch_user",
          args: {
            request_data: {
              company_name: companyName,
            },
          },
          callback: (response) => {},
          error: (error) => {
            // Error Handling is Defered to the Server
          },
        });
      }
    );
  },
};
