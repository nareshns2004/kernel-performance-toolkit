/*
 * mem_latency: load-to-use latency vs working-set size, by pointer chasing.
 *
 * Each element stores the index of the next one, in a random cyclic permutation
 * (Sattolo's algorithm), so every load depends on the previous one: no
 * memory-level parallelism, and the hardware prefetcher can't guess the next
 * address. ns/load vs size shows the L1 -> L2 -> L3 -> DRAM cliffs, and with
 * --huge the TLB-reach difference between 4 KiB and 2 MiB pages.
 *
 *   ./mem_latency                     # 4 KiB .. 1 GiB, base pages
 *   ./mem_latency --huge              # same, madvise(MADV_HUGEPAGE)
 *   numactl --cpunodebind=0 --membind=1 ./mem_latency   # remote-node latency
 *
 * Output: CSV  size_bytes,ns_per_load,huge
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

static uint64_t rng_state = 0x9E3779B97F4A7C15ull;
static uint64_t xorshift(void) {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return rng_state;
}

static double now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e9 + ts.tv_nsec;
}

static double chase(size_t bytes, int huge, size_t min_loads) {
    size_t n = bytes / sizeof(uint64_t);
    /* One cache line per element would hide spatial locality; one u64 per element
       with a random permutation already defeats it while keeping the size exact. */
    uint64_t *a = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (a == MAP_FAILED) return -1;
    if (huge) madvise(a, bytes, MADV_HUGEPAGE);
    for (size_t i = 0; i < n; i++) a[i] = i;
    for (size_t i = n - 1; i > 0; i--) { /* Sattolo: a single cycle through all elements */
        size_t j = xorshift() % i;
        uint64_t t = a[i]; a[i] = a[j]; a[j] = t;
    }
    size_t loads = n * 4 > min_loads ? n * 4 : min_loads;
    volatile uint64_t p = 0;
    for (size_t i = 0; i < n; i++) p = a[p]; /* warm up caches and TLB */
    double t0 = now_ns();
    for (size_t i = 0; i < loads; i++) p = a[p];
    double dt = now_ns() - t0;
    munmap(a, bytes);
    return dt / loads + 0 * (double)p;
}

int main(int argc, char **argv) {
    int huge = argc > 1 && strcmp(argv[1], "--huge") == 0;
    size_t max_bytes = (size_t)1 << 30;
    printf("size_bytes,ns_per_load,huge\n");
    for (size_t b = 4096; b <= max_bytes; b *= 2) {
        printf("%zu,%.2f,%d\n", b, chase(b, huge, 20 * 1000 * 1000), huge);
        fflush(stdout);
    }
    return 0;
}
