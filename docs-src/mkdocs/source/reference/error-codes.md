# Error Codes

Generated from config/error_codes.yaml (catalog 1.1.0). Do not edit by hand.

Published code meanings are permanent.

## ALEMS-ATTR-0001 Residual not computed { #err-attr-0001 }

**Meaning:** the residual step had no store connection and produced no row

Severity error; recoverable True; affects validity partial; stage affinity residual; status active.

## ALEMS-CFG-0001 Config file missing { #err-cfg-0001 }

**Meaning:** a required configuration file does not exist

Severity error; recoverable False; affects validity invalid; stage affinity setup; status active.

## ALEMS-CFG-0002 YAML syntax error { #err-cfg-0002 }

**Meaning:** a YAML file could not be parsed; file, line, and column are in the message

Severity error; recoverable False; affects validity invalid; stage affinity setup; status active.

## ALEMS-CFG-0003 JSON syntax error { #err-cfg-0003 }

**Meaning:** a JSON file could not be parsed

Severity error; recoverable False; affects validity invalid; stage affinity setup; status active.

## ALEMS-CFG-0004 Config schema violation { #err-cfg-0004 }

**Meaning:** a configuration file fails its JSON Schema

Severity error; recoverable False; affects validity invalid; stage affinity setup; status active.

## ALEMS-CFG-0005 Unknown config key { #err-cfg-0005 }

**Meaning:** a key not declared in the schema was rejected

Severity error; recoverable False; affects validity invalid; stage affinity setup; status active.

## ALEMS-CFG-0006 Same layer collision { #err-cfg-0006 }

**Meaning:** two sources in one precedence layer set the same key

Severity error; recoverable False; affects validity invalid; stage affinity setup; status active.

## ALEMS-CFG-0007 Forbidden key in layer { #err-cfg-0007 }

**Meaning:** hardware, endpoint, or credential set outside the machine layer

Severity error; recoverable False; affects validity invalid; stage affinity setup; status active.

## ALEMS-CFG-0008 Credential missing { #err-cfg-0008 }

**Meaning:** no credential found for the selected provider

Severity error; recoverable False; affects validity invalid; stage affinity setup; status active.

## ALEMS-CFG-0009 Hardware config accessor failed { #err-cfg-0009 }

**Meaning:** resolve_hw_config raised; the legacy direct load of hw_config.json was used instead

Severity warn; recoverable True; affects validity none; stage affinity setup; status active.

**Causes:** machine config path unreadable; data root not configured

**Actions:** run alems sandbox doctor; check <data_root>/<host>/config/hw_config.json

## ALEMS-CFG-0010 Data root not configured { #err-cfg-0010 }

**Meaning:** no data root and no explicit log and error directories are configured; a run cannot start because logs and error records have nowhere to go

Severity fatal; recoverable False; affects validity invalid; stage affinity setup; status active.

**Causes:** ALEMS_DATA_ROOT not set; ~/.alemsrc missing or failed to load

**Actions:** add export ALEMS_DATA_ROOT=/path to ~/.alemsrc; or export ALEMS_DATA_ROOT in the shell; or export ALEMS_LOG_DIR and ALEMS_ERROR_DIR; verify with alems sandbox doctor

## ALEMS-ETL-0101 GPU SPBM ETL failed { #err-etl-0101 }

**Meaning:** gpu_spbm_etl did not complete for the run; derived rows may be missing

Severity warn; recoverable True; affects validity partial; stage affinity etl_hardware; status active.

**Actions:** read the error record traceback; rerun the ETL with --run-id

## ALEMS-ETL-0102 SPBM telemetry ETL failed { #err-etl-0102 }

**Meaning:** spbm_telemetry_etl did not complete for the run; derived rows may be missing

Severity warn; recoverable True; affects validity partial; stage affinity etl_hardware; status active.

**Actions:** read the error record traceback; rerun the ETL with --run-id

## ALEMS-ETL-0103 Network ETL failed { #err-etl-0103 }

**Meaning:** network_etl did not complete for the run; derived rows may be missing

Severity warn; recoverable True; affects validity partial; stage affinity etl_hardware; status active.

**Actions:** read the error record traceback; rerun the ETL with --run-id

## ALEMS-GEN-0000 Unclassified failure { #err-gen-0000 }

**Meaning:** A failure occurred that no catalog code or rule describes yet

Severity error; recoverable False; affects validity none; stage affinity none; status active.

**Actions:** read the error record traceback; add a code or rule in config/error_codes.yaml

## ALEMS-NET-0001 DNS resolution failed { #err-net-0001 }

**Meaning:** the host name could not be resolved

Severity error; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-NET-0002 Connection failed { #err-net-0002 }

**Meaning:** connection refused or host unreachable (network down, server not running)

Severity error; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-NET-0003 TLS failure { #err-net-0003 }

**Meaning:** the TLS handshake or certificate check failed

Severity error; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-NET-0004 Timeout { #err-net-0004 }

**Meaning:** the operation exceeded its timeout

Severity warn; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-NET-0005 Connection reset { #err-net-0005 }

**Meaning:** the connection was reset during the request

Severity warn; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-OBS-0001 Error capture failed { #err-obs-0001 }

**Meaning:** an error occurred but its record could not be captured; the original exception is unaffected

Severity warn; recoverable True; affects validity none; stage affinity none; status active.

## ALEMS-OBS-0002 Observability buffer overflow { #err-obs-0002 }

**Meaning:** a non lossy buffer refused records inside the measurement window

Severity error; recoverable True; affects validity invalid; stage affinity none; status active.

## ALEMS-OBS-0003 Gate consumer failed { #err-obs-0003 }

**Meaning:** a gate consumer raised while receiving records

Severity warn; recoverable True; affects validity none; stage affinity none; status active.

## ALEMS-OBS-0004 Stage row write failed { #err-obs-0004 }

**Meaning:** stage_event rows for the run could not be written

Severity warn; recoverable True; affects validity none; stage affinity none; status active.

## ALEMS-PERS-0001 Run row not inserted { #err-pers-0001 }

**Meaning:** insert_run returned no run_id; the run has no runs row

Severity error; recoverable False; affects validity invalid; stage affinity persist_run; status active.

**Causes:** writer rejected the insert; schema mismatch

**Actions:** run alems migrate check; read the host log for the writer error

## ALEMS-PERS-0101 Device telemetry not persisted { #err-pers-0101 }

**Meaning:** device_telemetry rows for the run could not be written; run energy is unaffected

Severity warn; recoverable True; affects validity partial; stage affinity persist_samples; status active.

**Causes:** writer rejected the batch; schema mismatch; malformed reader buffer

**Actions:** read the error record traceback; run alems migrate check

## ALEMS-PERS-0102 Power rail samples not persisted { #err-pers-0102 }

**Meaning:** power_rail rows for the run could not be written; run energy is unaffected

Severity warn; recoverable True; affects validity partial; stage affinity persist_samples; status active.

**Causes:** writer rejected the batch; schema mismatch; malformed reader buffer

**Actions:** read the error record traceback; run alems migrate check

## ALEMS-PERS-0103 CPU idle states (ARM) not persisted { #err-pers-0103 }

**Meaning:** cpu_idle_states rows for the run could not be written; run energy is unaffected

Severity warn; recoverable True; affects validity partial; stage affinity persist_samples; status active.

**Causes:** writer rejected the batch; schema mismatch; malformed reader buffer

**Actions:** read the error record traceback; run alems migrate check

## ALEMS-PERS-0104 CPU idle states (x86) not persisted { #err-pers-0104 }

**Meaning:** cpu_idle_states rows for the run could not be written; run energy is unaffected

Severity warn; recoverable True; affects validity partial; stage affinity persist_samples; status active.

**Causes:** writer rejected the batch; schema mismatch; malformed reader buffer

**Actions:** read the error record traceback; run alems migrate check

## ALEMS-PERS-0105 Thermal samples not persisted { #err-pers-0105 }

**Meaning:** thermal_samples_v2 rows for the run could not be written; run energy is unaffected

Severity warn; recoverable True; affects validity partial; stage affinity persist_samples; status active.

**Causes:** writer rejected the batch; schema mismatch; malformed reader buffer

**Actions:** read the error record traceback; run alems migrate check

## ALEMS-PERS-0106 Cooling samples not persisted { #err-pers-0106 }

**Meaning:** cooling_samples rows for the run could not be written; run energy is unaffected

Severity warn; recoverable True; affects validity partial; stage affinity persist_samples; status active.

**Causes:** writer rejected the batch; schema mismatch; malformed reader buffer

**Actions:** read the error record traceback; run alems migrate check

## ALEMS-PERS-0107 Summary CPU sample not persisted { #err-pers-0107 }

**Meaning:** cpu_samples rows for the run could not be written; run energy is unaffected

Severity warn; recoverable True; affects validity partial; stage affinity persist_samples; status active.

**Causes:** writer rejected the batch; schema mismatch; malformed reader buffer

**Actions:** read the error record traceback; run alems migrate check

## ALEMS-PROV-0001 Authentication failed { #err-prov-0001 }

**Meaning:** the provider rejected the credential (wrong, expired, or revoked key)

Severity error; recoverable False; affects validity invalid; stage affinity none; status active.

## ALEMS-PROV-0002 Access denied { #err-prov-0002 }

**Meaning:** the credential is valid but lacks access to the model or resource

Severity error; recoverable False; affects validity invalid; stage affinity none; status active.

## ALEMS-PROV-0101 Model or endpoint not found { #err-prov-0101 }

**Meaning:** the provider returned 404 for the model or endpoint

Severity error; recoverable False; affects validity invalid; stage affinity none; status active.

## ALEMS-PROV-0102 Rate limited { #err-prov-0102 }

**Meaning:** the provider throttled the request or the quota is exhausted (429)

Severity warn; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-PROV-0103 Request rejected { #err-prov-0103 }

**Meaning:** the provider rejected the request as invalid (400, 422; context length, bad parameter)

Severity error; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-PROV-0201 Provider server error { #err-prov-0201 }

**Meaning:** the provider returned a 5xx response

Severity warn; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-PROV-0202 Malformed response { #err-prov-0202 }

**Meaning:** the provider response could not be parsed or was truncated

Severity warn; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-PROV-0203 Content refused { #err-prov-0203 }

**Meaning:** the provider refused or filtered the content

Severity info; recoverable True; affects validity none; stage affinity none; status active.

## ALEMS-SPAN-0001 Span building failed { #err-span-0001 }

**Meaning:** spans for the run could not be built from the agent trace

Severity error; recoverable True; affects validity partial; stage affinity spans; status active.

## ALEMS-SPAN-0002 Span writing failed { #err-span-0002 }

**Meaning:** built spans could not be written

Severity error; recoverable True; affects validity partial; stage affinity spans; status active.

## ALEMS-TOOL-0001 Tool raised { #err-tool-0001 }

**Meaning:** a tool raised during execution

Severity warn; recoverable True; affects validity none; stage affinity none; status active.

## ALEMS-TOOL-0002 Tool not found { #err-tool-0002 }

**Meaning:** the requested tool is not in the catalog

Severity error; recoverable True; affects validity partial; stage affinity none; status active.

## ALEMS-TOOL-0003 Tool arguments invalid { #err-tool-0003 }

**Meaning:** tool arguments fail the tool parameter schema

Severity warn; recoverable True; affects validity none; stage affinity none; status active.
