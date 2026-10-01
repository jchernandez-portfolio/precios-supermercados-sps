let
    SourceUrl = "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/precios-supermercados-sps/published/bi/la-colonia-walmart-sps/dataset.json",
    Raw = Json.Document(Web.Contents(SourceUrl, [Headers = [Accept = "application/json"]])),
    ExpectedSchema = "precios-sps-static-bi-dataset/v1",
    ExpectedPolicy = "fail_closed_strong_identity_and_commercial_consistency",
    SchemaChecked =
        if Record.FieldOrDefault(Raw, "schema", null) = ExpectedSchema
        then Raw
        else error "precios_sps_static_dataset_schema_invalid",
    PolicyChecked =
        if Record.FieldOrDefault(SchemaChecked, "comparison_policy", null) = ExpectedPolicy
        then SchemaChecked
        else error "precios_sps_static_dataset_policy_invalid",
    Publication = Record.FieldOrDefault(PolicyChecked, "publication", null),
    Manifest = Record.FieldOrDefault(PolicyChecked, "manifest", null),
    Checked =
        if Publication is record and Manifest is record
        then PolicyChecked
        else error "precios_sps_static_dataset_contract_invalid"
in
    Checked
