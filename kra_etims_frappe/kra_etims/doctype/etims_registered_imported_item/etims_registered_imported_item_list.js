const doctypeName = "eTims Registered Imported Item";

frappe.listview_settings[doctypeName] = {
  onload: function (listview) {
    const companyName = frappe.boot.sysdefaults.company;

    listview.page.add_inner_button(
      __("Get Imported Items"),
      function (listview) {
        frappe.call({
          method:
            "kra_etims_frappe.kra_etims.apis.apis.perform_import_item_search_all_branches",
          args: {
            request_data: {
              company_name: companyName,
            },
          },
          freeze: true,
          freeze_message: __("Sending request to eTIMS… Please wait"),          
          callback: (response) => {},
          error: (error) => {
            // Error Handling is Defered to the Server
          },
        });
      }
    );
  },
};
