/*
 * page_fault_cost: what a first-touch page fault costs, and how to avoid paying it.
 *
 * Touches a fresh anonymous mapping one byte per 4 KiB in four modes:
 *   base      - plain mmap, one minor fault per 4 KiB page
 *   populate  - MAP_POPULATE, faults taken up front inside mmap()
 *   thp       - madvise(MADV_HUGEPAGE), ideally one fault per 2 MiB
 *   prefault  - touch once, then time a second pass (no faults: the floor)
 * Fault counts come from getrusage(RUSAGE_SELF).ru_minflt.
 *
 *   ./page_fault_cost [MiB=512]
 * Output: CSV  mode,mib,ms,minor_faults,ns_per_4k_page
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <time.h>

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

static long minflt(void) {
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    return ru.ru_minflt;
}

static void run(const char *mode, size_t mib) {
    size_t bytes = mib << 20, pages = bytes / 4096;
    int flags = MAP_PRIVATE | MAP_ANONYMOUS | (strcmp(mode, "populate") == 0 ? MAP_POPULATE : 0);
    long f0 = minflt();
    double t0 = now_ms();
    char *p = mmap(NULL, bytes, PROT_READ | PROT_WRITE, flags, -1, 0);
    if (p == MAP_FAILED) { perror("mmap"); exit(1); }
    if (strcmp(mode, "thp") == 0) madvise(p, bytes, MADV_HUGEPAGE);
    if (strcmp(mode, "prefault") == 0) {
        for (size_t i = 0; i < pages; i++) p[i * 4096] = 1;
        f0 = minflt();
        t0 = now_ms();
    }
    for (size_t i = 0; i < pages; i++) p[i * 4096] = (char)i;
    double ms = now_ms() - t0;
    long faults = minflt() - f0;
    printf("%s,%zu,%.2f,%ld,%.1f\n", mode, mib, ms, faults, ms * 1e6 / pages);
    munmap(p, bytes);
}

int main(int argc, char **argv) {
    size_t mib = argc > 1 ? strtoull(argv[1], NULL, 10) : 512;
    printf("mode,mib,ms,minor_faults,ns_per_4k_page\n");
    run("base", mib);
    run("populate", mib);
    run("thp", mib);
    run("prefault", mib);
    return 0;
}
