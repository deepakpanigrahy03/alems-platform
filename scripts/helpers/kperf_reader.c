/*
 * alems_kperf_reader.c
 *
 * Minimal helper for A-LEMS: reads Apple Silicon PMU counters via
 * kperf/kperfdata private frameworks. Prints JSON to stdout.
 *
 * Build:  cc -O2 -o kperf_reader kperf_reader.c
 * Usage:  sudo ./kperf_reader
 * Output: {"instructions":123456,"cycles":789012,
 *          "l1d_miss_ld":34,"l1d_tlb_access":5000}
 *
 * Exit codes: 0 = success, 1 = framework load failed,
 *             2 = kpep db failed, 3 = counter config failed,
 *             4 = counter read failed
 *
 * Requires root (kpc_set_config needs EPERM bypass).
 * System-wide counters (all CPUs, all processes).
 *
 * Counter layout on Apple Silicon (verified on M1 Pro):
 *   fixed=2, configurable=6, combined=8
 *   Combined buffer: [0]=FIXED_CYCLES [1]=FIXED_INSTRUCTIONS [2-7]=configurable
 *   kpc_map returns absolute index into combined buffer.
 *   This layout is generic across all Apple Silicon generations.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <dlfcn.h>

extern int sysctl(int *, unsigned int, void *, size_t *, void *, size_t);

/* Counter class bitmasks */
#define KPC_CLASS_FIXED          (1u << 0)
#define KPC_CLASS_CONFIGURABLE   (1u << 1)
#define KPC_CLASS_FIXED_AND_CFG  (KPC_CLASS_FIXED | KPC_CLASS_CONFIGURABLE)

/* Conservative upper bound — Apple Silicon has 2 fixed + 6-8 configurable */
#define MAX_COUNTERS 16

/* kperf function pointer typedefs */
typedef uint32_t (*kpc_get_counter_count_fn)(uint32_t classes);
typedef int      (*kpc_set_counting_fn)(uint32_t classes);
typedef int      (*kpc_set_config_fn)(uint32_t classes, uint64_t *config);
typedef int      (*kpc_get_cpu_counters_fn)(int all_cpus, uint32_t classes,
                                            int cpu, uint64_t *buf);
typedef int      (*kpc_force_all_ctrs_set_fn)(int enable);

/* kpep function pointer typedefs */
typedef int  (*kpep_db_create_fn)(const char *path, void **db_out);
typedef void (*kpep_db_free_fn)(void *db);
typedef int  (*kpep_db_event_fn)(void *db, const char *name, void **ev_out);
typedef int  (*kpep_config_create_fn)(void *db, void **cfg_out);
typedef void (*kpep_config_free_fn)(void *cfg);
typedef int  (*kpep_config_add_event_fn)(void *cfg, void **ev,
                                         uint32_t flag, uint32_t *err);
typedef int  (*kpep_config_kpc_fn)(void *cfg, uint64_t *buf, size_t buf_size);
typedef int  (*kpep_config_kpc_count_fn)(void *cfg, size_t *count_out);
typedef int  (*kpep_config_kpc_classes_fn)(void *cfg, uint32_t *classes_out);
typedef int  (*kpep_config_kpc_map_fn)(void *cfg, size_t *buf, size_t buf_size);

/* Global function pointers */
static kpc_get_counter_count_fn  kpc_get_counter_count;
static kpc_set_counting_fn       kpc_set_counting;
static kpc_set_config_fn         kpc_set_config;
static kpc_get_cpu_counters_fn   kpc_get_cpu_counters;
static kpc_force_all_ctrs_set_fn kpc_force_all_ctrs_set;

static kpep_db_create_fn         kpep_db_create;
static kpep_db_free_fn           kpep_db_free;
static kpep_db_event_fn          kpep_db_event;
static kpep_config_create_fn     kpep_config_create;
static kpep_config_free_fn       kpep_config_free;
static kpep_config_add_event_fn  kpep_config_add_event;
static kpep_config_kpc_fn        kpep_config_kpc;
static kpep_config_kpc_count_fn  kpep_config_kpc_count;
static kpep_config_kpc_classes_fn kpep_config_kpc_classes;
static kpep_config_kpc_map_fn    kpep_config_kpc_map;

static int load_frameworks(void)
{
    void *kperf = dlopen(
        "/System/Library/PrivateFrameworks/kperf.framework/kperf", RTLD_LAZY);
    if (!kperf) {
        fprintf(stderr, "ERROR: cannot load kperf.framework: %s\n", dlerror());
        return 1;
    }
    void *kperfdata = dlopen(
        "/System/Library/PrivateFrameworks/kperfdata.framework/kperfdata",
        RTLD_LAZY);
    if (!kperfdata) {
        fprintf(stderr, "ERROR: cannot load kperfdata.framework: %s\n",
                dlerror());
        return 1;
    }

    kpc_get_counter_count  = dlsym(kperf, "kpc_get_counter_count");
    kpc_set_counting       = dlsym(kperf, "kpc_set_counting");
    kpc_set_config         = dlsym(kperf, "kpc_set_config");
    kpc_get_cpu_counters   = dlsym(kperf, "kpc_get_cpu_counters");
    kpc_force_all_ctrs_set = dlsym(kperf, "kpc_force_all_ctrs_set");

    kpep_db_create          = dlsym(kperfdata, "kpep_db_create");
    kpep_db_free            = dlsym(kperfdata, "kpep_db_free");
    kpep_db_event           = dlsym(kperfdata, "kpep_db_event");
    kpep_config_create      = dlsym(kperfdata, "kpep_config_create");
    kpep_config_free        = dlsym(kperfdata, "kpep_config_free");
    kpep_config_add_event   = dlsym(kperfdata, "kpep_config_add_event");
    kpep_config_kpc         = dlsym(kperfdata, "kpep_config_kpc");
    kpep_config_kpc_count   = dlsym(kperfdata, "kpep_config_kpc_count");
    kpep_config_kpc_classes = dlsym(kperfdata, "kpep_config_kpc_classes");
    kpep_config_kpc_map     = dlsym(kperfdata, "kpep_config_kpc_map");

    if (!kpc_get_counter_count || !kpc_set_counting || !kpc_set_config ||
        !kpc_get_cpu_counters  || !kpc_force_all_ctrs_set ||
        !kpep_db_create || !kpep_db_event || !kpep_config_create ||
        !kpep_config_add_event || !kpep_config_kpc ||
        !kpep_config_kpc_count || !kpep_config_kpc_classes ||
        !kpep_config_kpc_map) {
        fprintf(stderr, "ERROR: failed to resolve required kperf symbols\n");
        return 1;
    }
    return 0;
}

/*
 * Configurable events to request.
 *
 * L1D_CACHE_MISS_LD: L1D load cache misses (speculative + retired).
 *   Available on all Apple Silicon via a14/a15/a16/a17 plist.
 *
 * L1D_TLB_ACCESS: L1D TLB accesses. Used as proxy for cache_references
 *   since no direct total-accesses event exists on Apple Silicon.
 *
 * L1D_CACHE_MISS_LD_NONSPEC was removed: it conflicts with L1D_CACHE_MISS_LD
 * for the same hardware counter slot on M1 Pro (ret=13, EACCES).
 * L1D_CACHE_MISS_LD is used instead — slightly less precise (includes
 * speculative misses) but available without slot conflict.
 */
static const char *CFG_EVENT_NAMES[] = {
    "L1D_CACHE_MISS_LD",
    "L1D_TLB_ACCESS",
};
#define NUM_CFG_EVENTS 2

int main(void)
{
    int ret;

    if (load_frameworks() != 0)
        return 1;

    /* Create kpep database — NULL path auto-detects chip from /usr/share/kpep/ */
    void *db = NULL;
    ret = kpep_db_create(NULL, &db);
    if (ret != 0 || !db) {
        fprintf(stderr, "ERROR: kpep_db_create failed (ret=%d)\n", ret);
        return 2;
    }

    /* Create kpep config and add configurable events */
    void *cfg = NULL;
    ret = kpep_config_create(db, &cfg);
    if (ret != 0 || !cfg) {
        fprintf(stderr, "ERROR: kpep_config_create failed (ret=%d)\n", ret);
        kpep_db_free(db);
        return 2;
    }

    /*
     * Add each event to config. Track which events succeeded via
     * event_added[] so we can map output correctly even if some fail.
     */
    int event_added[NUM_CFG_EVENTS];
    int events_added = 0;
    for (int i = 0; i < NUM_CFG_EVENTS; i++) {
        void *ev = NULL;
        ret = kpep_db_event(db, CFG_EVENT_NAMES[i], &ev);
        if (ret != 0 || !ev) {
            fprintf(stderr, "WARN: event %s not in chip plist, skipping\n",
                    CFG_EVENT_NAMES[i]);
            event_added[i] = 0;
            continue;
        }
        uint32_t err = 0;
        ret = kpep_config_add_event(cfg, &ev, 0, &err);
        if (ret != 0) {
            fprintf(stderr, "WARN: kpep_config_add_event(%s) failed "
                    "(ret=%d, err=%u)\n", CFG_EVENT_NAMES[i], ret, err);
            event_added[i] = 0;
            continue;
        }
        event_added[i] = 1;
        events_added++;
    }

    /* Get KPC config classes — tells us which counter classes kpep assigned */
    uint32_t classes = 0;
    ret = kpep_config_kpc_classes(cfg, &classes);
    if (ret != 0) {
        fprintf(stderr, "ERROR: kpep_config_kpc_classes failed\n");
        kpep_config_free(cfg); kpep_db_free(db);
        return 3;
    }
    classes |= KPC_CLASS_FIXED;  /* always read fixed counters */

    /* Get KPC config array — the hardware event selector values */
    uint64_t kpc_config[MAX_COUNTERS];
    memset(kpc_config, 0, sizeof(kpc_config));
    ret = kpep_config_kpc(cfg, kpc_config, sizeof(kpc_config));
    if (ret != 0) {
        fprintf(stderr, "ERROR: kpep_config_kpc failed (ret=%d)\n", ret);
        kpep_config_free(cfg); kpep_db_free(db);
        return 3;
    }

    /*
     * Get kpc_map: maps each successfully-added event (in order of addition)
     * to its absolute index in the COMBINED counter buffer.
     * Combined buffer layout: [0..n_fixed-1]=fixed, [n_fixed..total-1]=cfg.
     * Buffer size must be exactly events_added * sizeof(size_t).
     */
    size_t kpc_map[NUM_CFG_EVENTS];
    memset(kpc_map, 0, sizeof(kpc_map));
    if (events_added > 0) {
        ret = kpep_config_kpc_map(cfg, kpc_map, events_added * sizeof(size_t));
        if (ret != 0) {
            fprintf(stderr, "WARN: kpep_config_kpc_map failed (ret=%d), "
                    "using fallback mapping\n", ret);
            /*
             * Fallback: configurable events start after fixed counters.
             * n_fixed is always 2 on Apple Silicon (verified M1-M4).
             */
            uint32_t n_fixed = kpc_get_counter_count(KPC_CLASS_FIXED);
            if (n_fixed == 0) n_fixed = 2;
            for (int i = 0; i < events_added; i++)
                kpc_map[i] = n_fixed + i;
        }
    }

    /* Apply hardware counter configuration (requires root) */
    ret = kpc_force_all_ctrs_set(1);
    if (ret != 0) {
        fprintf(stderr, "ERROR: kpc_force_all_ctrs_set failed (ret=%d). "
                "Run as root.\n", ret);
        kpep_config_free(cfg); kpep_db_free(db);
        return 3;
    }
    ret = kpc_set_config(classes, kpc_config);
    if (ret != 0) {
        fprintf(stderr, "ERROR: kpc_set_config failed (ret=%d)\n", ret);
        kpep_config_free(cfg); kpep_db_free(db);
        return 3;
    }
    ret = kpc_set_counting(classes);
    if (ret != 0) {
        fprintf(stderr, "ERROR: kpc_set_counting failed (ret=%d)\n", ret);
        kpep_config_free(cfg); kpep_db_free(db);
        return 3;
    }

    /* Get total combined counter count for buffer sizing */
    uint32_t total_counters = kpc_get_counter_count(KPC_CLASS_FIXED_AND_CFG);
    if (total_counters == 0 || total_counters > MAX_COUNTERS)
        total_counters = 8;  /* verified default on all Apple Silicon */

    /* Get CPU count for per-CPU buffer sizing */
    int ncpus = 0;
    {
        size_t sz = sizeof(ncpus);
        int mib[] = {6 /* CTL_HW */, 3 /* HW_NCPU */};
        sysctl(mib, 2, &ncpus, &sz, NULL, 0);
        if (ncpus <= 0) ncpus = 1;
    }

    /*
     * Read ALL counters (fixed + configurable) in ONE call.
     * kpc_get_cpu_counters with all_cpus=1 returns ncpus * total_counters
     * values laid out as: [cpu0_ctr0, cpu0_ctr1, ..., cpu1_ctr0, ...].
     * Combined buffer: [0..n_fixed-1]=fixed, [n_fixed..total-1]=configurable.
     */
    size_t buf_size = ncpus * total_counters;
    uint64_t *buf = calloc(buf_size, sizeof(uint64_t));
    if (!buf) {
        fprintf(stderr, "ERROR: calloc failed\n");
        kpep_config_free(cfg); kpep_db_free(db);
        return 4;
    }
    ret = kpc_get_cpu_counters(1, classes, 0, buf);
    if (ret != 0) {
        fprintf(stderr, "ERROR: kpc_get_cpu_counters failed (ret=%d)\n", ret);
        free(buf); kpep_config_free(cfg); kpep_db_free(db);
        return 4;
    }

    /* Sum each counter slot across all CPUs */
    uint64_t sums[MAX_COUNTERS];
    memset(sums, 0, sizeof(sums));
    for (int cpu = 0; cpu < ncpus; cpu++) {
        for (uint32_t c = 0; c < total_counters; c++) {
            sums[c] += buf[(size_t)cpu * total_counters + c];
        }
    }
    free(buf);

    /*
     * Extract fixed counter values.
     * Fixed slot 0 = FIXED_CYCLES, slot 1 = FIXED_INSTRUCTIONS.
     * This ordering is consistent across all Apple Silicon generations.
     */
    uint64_t fixed_cycles       = sums[0];
    uint64_t fixed_instructions = sums[1];

    /*
     * Extract configurable event values.
     * kpc_map is unreliable on Apple Silicon — it returns fixed counter
     * indices (0,1) instead of configurable indices (n_fixed+). 
     * Direct indexing: configurable events start at sums[n_fixed].
     * Verified: sums[0..n_fixed-1]=fixed, sums[n_fixed..]=configurable.
     * This is consistent across all Apple Silicon generations.
     */
    uint32_t n_fixed = kpc_get_counter_count(KPC_CLASS_FIXED);
    if (n_fixed == 0) n_fixed = 2;

    uint64_t cfg_values[NUM_CFG_EVENTS];
    memset(cfg_values, 0, sizeof(cfg_values));
    int cfg_slot = 0;
    for (int i = 0; i < NUM_CFG_EVENTS; i++) {
        if (!event_added[i]) {
            cfg_values[i] = 0;
            continue;
        }
        size_t abs_slot = n_fixed + cfg_slot;
        cfg_values[i] = (abs_slot < MAX_COUNTERS) ? sums[abs_slot] : 0;
        cfg_slot++;
    }

    /* Debug: print kpc_map values to stderr */
    fprintf(stderr, "DEBUG: n_fixed=%u total=%u events_added=%d\n",
            kpc_get_counter_count(KPC_CLASS_FIXED),
            total_counters, events_added);
    for (int i = 0; i < events_added; i++)
        fprintf(stderr, "DEBUG: kpc_map[%d]=%zu -> sums[%zu]=%llu\n",
                i, kpc_map[i], kpc_map[i],
                kpc_map[i] < MAX_COUNTERS ? sums[kpc_map[i]] : 0);
    fprintf(stderr, "DEBUG: sums[0]=%llu sums[1]=%llu sums[2]=%llu sums[3]=%llu\n",
            sums[0], sums[1], sums[2], sums[3]);

    /* Print JSON. Index: 0=L1D_CACHE_MISS_LD, 1=L1D_TLB_ACCESS */
    printf("{\"instructions\":%llu,\"cycles\":%llu,"
           "\"l1d_miss_ld\":%llu,\"l1d_tlb_access\":%llu}\n",
           fixed_instructions, fixed_cycles,
           cfg_values[0], cfg_values[1]);

    kpep_config_free(cfg);
    kpep_db_free(db);
    return 0;
}