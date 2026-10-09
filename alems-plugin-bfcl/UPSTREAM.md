# Vendored Upstream: BFCL Multi Turn

Repository: https://github.com/ShishirPatil/gorilla (berkeley-function-call-leaderboard)
Commit: 6ea57973c7a6097fd7c5915698c54c17c5b1b6c8 (2026-03-23)
License: Apache License 2.0, full text in LICENSE.upstream
Version: BFCL v4 file names; multi turn base and long_context categories only.

## Files and Modifications

Source classes, from bfcl_eval/eval_checker/multi_turn_eval/func_source_code/, in alems_plugin_bfcl/upstream/:

| File | SHA256 after modification |
|---|---|
| gorilla_file_system.py | 6aed83544123eece4b1fc82e26b009c80e905bf11e8c3acb1b5cd8b5dc9b03db |
| long_context.py | 5c9a5710115609c779e5b2ccb43f6ad58271ed96f2ea7a797edb0d65c385aa38 |
| math_api.py | abde32cc25e71b2ff3acf4d028a3c005b98409c174aba4abddbfe1c018afb9a8 |
| message_api.py | cf72af1635654147af17df1891a7fb354549e78a61930139f741a31f4be21121 |
| posting_api.py | 32b887aaa37c7167ad9c84ddfa7bbb855c75d3c1edab19ba868aba29acc4cfa1 |
| ticket_api.py | 6a9375aa6425410c07a151aef27f3e44e9ed1023bb5cc5c25f14f4a9b3c99116 |
| trading_bot.py | 883d24eeb24d369d2331c8f1c626732957ea87bb75105e87c752315cb604a058 |
| travel_booking.py | 715aeffadf41d48476260e79537e29df8bdb8f6c3bc4156a71bca6ddc20f2de3 |
| vehicle_control.py | ddae58c329b0cd7dc4cd8cd24d5c192561b26eaa9de6c3a1b08dbe0130b31ce5 |

Only modification: in gorilla_file_system.py, trading_bot.py, travel_booking.py and vehicle_control.py the line
`from bfcl_eval.eval_checker.multi_turn_eval.func_source_code.long_context import (`
became
`from .long_context import (`.
No other byte changed.

Data files, from bfcl_eval/data/, unmodified, in alems_plugin_bfcl/data/:

| File | SHA256 |
|---|---|
| BFCL_v4_multi_turn_base.json | 1a21a995d06fd6f20ba55de7bced30ef953ec35e998f502ec2ecf4d66ef1c43a |
| BFCL_v4_multi_turn_long_context.json | 78c3268c5cc8e97c0f4ec6c811b3b9a2bba14323b1830b7a874b06d822749324 |
| possible_answer/BFCL_v4_multi_turn_base.json | 1fee67823b317571649177dd89d63969feaae4e810cc7448ee55ba797fb7c8fc |
| possible_answer/BFCL_v4_multi_turn_long_context.json | e82aa0e839c39d23c64a05834f7ae024d7c9738ec21738ecee78dc876e3d0d18 |
| multi_turn_func_doc/gorilla_file_system.json | c4c1b741c71e2a17c97a5dc9c4a91d89978c4eaece56494d14272d5df6c650e9 |
| multi_turn_func_doc/math_api.json | 83fa31708c89442bdcf12ac4dfbe3be8663ec9183a1b1524a9fda98164bc7e0b |
| multi_turn_func_doc/message_api.json | 4d58ea933a5d2b280d7a52366617fb47fecd333d7b0e08e724db6fa12fb5f847 |
| multi_turn_func_doc/posting_api.json | 87f9fc404a06e4107d7c366f1399c596440e84c16cb119faf374b2f532d86e8b |
| multi_turn_func_doc/ticket_api.json | 31324e0380782664fe82ba05bd23517808ad55692b45a5fe7c3d4d395fe4f0f8 |
| multi_turn_func_doc/trading_bot.json | 1a7933fd8f0cb8fbec38aeae05cb8c24132747cad4904fe1c13ac3d01fc22c2d |
| multi_turn_func_doc/travel_booking.json | f17b950c13adddf41d0848077df58788252e4c2e7cad5cfa71c8c4bf04f57b26 |
| multi_turn_func_doc/vehicle_control.json | 0c8a66292844874ef7b168f343bc394d8615d2d9e1f4387999a9ee23011eac78 |

## Not Vendored

web_search.py and the memory classes (serpapi, faiss, sentence_transformers dependencies; not in the multi turn base set). Upstream evaluation code (multi_turn_checker.py, multi_turn_utils.py) is reimplemented in scorer.py and env.py, not copied: execution uses ast parsing and bound method calls instead of eval on rewritten strings; result formatting, the forbidden name list, the error prefix, state comparison, and the unordered response check follow upstream exactly.

## Differences From Upstream Evaluation (declared)

1. Turns the agent never reached are scored as empty turns (upstream would index past the end).
2. Turns with an empty ground truth are executed but not judged, as in the upstream main checker. Six such turns exist (entries 167 and 180, both categories); the separate upstream irrelevance checker is not applied.
3. Method invoke order is not checked (disabled upstream as well).
