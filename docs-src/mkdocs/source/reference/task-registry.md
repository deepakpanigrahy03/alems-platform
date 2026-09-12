# Task Registry

A-LEMS ships with 65 tasks across text generation, reasoning, code,
speech, tool chains, and multi-step agentic workloads. Tasks are defined
in `config/tasks.yaml` and loaded into the `task_categories` database
table at install time.

---

## Listing Tasks

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 core/execution/tests/run_experiment.py --list-tasks
```

Output shows: task ID, name, difficulty level (1-3), and tool count.

---

## Task Categories

### Arithmetic and Reasoning

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `gsm8k_basic` | GSM8K Arithmetic | 1 | 0 |
| `gsm8k_multi_step` | Multi-step Arithmetic | 2 | 0 |
| `logical_reasoning` | Logical Deduction | 2 | 0 |
| `commonsense_reasoning` | Commonsense Reasoning | 1 | 0 |
| `t3_reasoning_hard` | Hard Reasoning — No Tools | 3 | 0 |
| `t3_planning_notools` | Experiment Planning — No Tools | 3 | 0 |

### Code Generation and Debugging

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `code_fibonacci` | Fibonacci Function | 2 | 0 |
| `code_sorting` | Sorting Algorithm | 2 | 0 |
| `bug_fixing` | Bug Fixing | 2 | 0 |
| `t3_debug_simple` | Simple Debug — No Tools | 1 | 0 |
| `debug_python_logic` | Debug Python Logic Error | 2 | 0 |
| `debug_with_file_check` | Debug with File Verification | 3 | 1 |
| `tg_code_execute` | Code Generation and Execute | 2 | 1 |

### Question Answering

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `factual_qa` | Factual Question | 1 | 0 |
| `science_qa` | Science Question | 1 | 0 |
| `geography_qa` | Geography Question | 1 | 0 |
| `triviaqa_t1_001` | TriviaQA Sample 1 | 1 | 0 |
| `triviaqa_t1_002` | TriviaQA Sample 2 | 1 | 0 |

### Summarization and Writing

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `news_summary` | News Article Summary | 1 | 0 |
| `research_summary` | Research Abstract Summary | 2 | 0 |
| `t3_summarization` | Technical Summarization | 2 | 0 |
| `t3_creative` | Technical Abstract Writing | 2 | 0 |
| `technical_blog_post` | Technical Blog Post | 2 | 0 |
| `research_abstract` | Research Abstract Writing | 3 | 0 |

### Classification and Extraction

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `sentiment_analysis` | Sentiment Analysis | 1 | 0 |
| `topic_classification` | Topic Classification | 1 | 0 |
| `entity_extraction` | Named Entity Extraction | 1 | 0 |
| `keyword_extraction` | Keyword Extraction | 1 | 0 |

### Multilingual

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `t3_translation_hi` | English to Hindi | 2 | 0 |
| `english_to_hindi` | English to Hindi Translation | 2 | 0 |
| `multilingual_summary` | Multilingual Summary | 3 | 0 |

### Tool-Chain Tasks (Agentic)

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `tg_single_db` | Single DB Query | 1 | 1 |
| `tg_single_calc` | Single Calculator | 1 | 1 |
| `tg_sequential_2` | Sequential 2-Tool Chain | 2 | 2 |
| `tg_sequential_3` | Sequential 3-Tool Chain | 3 | 3 |
| `tg_parallel_2` | Parallel 2-Tool Execution | 2 | 2 |
| `tg_deep_chain_4` | Deep 4-Tool Chain | 3 | 4 |
| `tg_search_synthesize` | Search and Synthesize | 2 | 1 |
| `tg_multi_search` | Multi-Search Research | 3 | 3 |
| `tg_error_recovery` | Tool Error Recovery | 3 | 2 |
| `tool_chain_execution` | Tool Chain Execution | 3 | 3 |

### Research and Analysis

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `db_query_analysis` | Database Query and Analysis | 2 | 2 |
| `file_analysis_pipeline` | File Analysis Pipeline | 2 | 2 |
| `multi_source_research` | Multi-Source Research | 3 | 3 |
| `experiment_planning` | Experiment Planning | 3 | 2 |
| `resource_optimization_plan` | Resource Optimization Plan | 3 | 2 |
| `energy_trend_analysis` | Energy Trend Analysis | 2 | 2 |
| `comparative_performance` | Comparative Performance | 2 | 1 |
| `literature_synthesis` | Research Synthesis | 3 | 2 |
| `methodology_comparison` | Methodology Comparison | 3 | 2 |
| `orchestration_overhead_measure` | Orchestration Overhead Measurement | 3 | 3 |
| `current_info_retrieval` | Current Information Retrieval | 2 | 1 |
| `multi_query_synthesis` | Multi-Query Synthesis | 3 | 2 |

### Benchmark Samples

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `gsm8k_t1_001` | GSM8K Sample 1 | 1 | 0 |
| `gsm8k_t1_002` | GSM8K Sample 2 | 2 | 0 |
| `humaneval_t1_001` | HumanEval Problem 1 | 2 | 0 |
| `humaneval_t1_002` | HumanEval Problem 2 | 2 | 0 |

### Speech and Voice

| Task ID | Name | Level | Tools |
|---|---|---|---|
| `tts_en_short` | TTS English Short | 1 | 0 |
| `tts_en_long` | TTS English Long | 2 | 0 |
| `tts_hi_short` | TTS Hindi Short | 1 | 0 |
| `tts_hi_long` | TTS Hindi Long | 2 | 0 |
| `stt_en_short` | STT English Short | 1 | 0 |
| `stt_en_long` | STT English Long | 2 | 0 |
| `vc_en_clone` | Voice Clone English | 2 | 0 |

---

## Difficulty Levels

| Level | Description |
|---|---|
| 1 | Single step, no tool calls, deterministic output |
| 2 | Multi-step or requires tool calls |
| 3 | Complex reasoning, multiple tool calls, or synthesis across sources |

---

## Running a Specific Task

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate

python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 3 \
  --provider groq \
  --workflow-mode comparison \
  --experiment-type normal \
  --experiment-goal "your research question" \
  --save-db
```

Run multiple tasks in one experiment:

```bash
python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic,logical_reasoning,code_fibonacci \
  --repetitions 1 \
  --provider groq \
  --workflow-mode comparison \
  --save-db
```

---

## Adding a Task

Task definitions live in `config/tasks.yaml`. See
`developer/adding-a-task.md` for the full process.
