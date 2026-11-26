"""
alems_plugin_workload: sequencer step kinds that generate controlled workloads.

request_profile  seeded, token sized chat requests to any OpenAI compatible
                 server (BASE, PREFILL, DECODE profiles defined in the plan)
matmul_load      fixed duty cycle matrix multiply, no model server needed

Imports only alems_sdk. Core owns timing, measurement and persistence; these
kinds return results as {key: (value, unit)} and never touch the store.
"""

__version__ = "0.1.0"
PROFILE_SPEC = "workload_conditions/1"  # version of the profile semantics in this plugin
