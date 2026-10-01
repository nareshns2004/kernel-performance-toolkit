/*
 * wakeup_latency: thread wakeup latency distribution via pipe ping-pong.
 *
 * Two threads bounce a byte through a pair of pipes; each round trip is two
 * block/wake cycles through the scheduler. Optional pinning shows the effect of
 * placement: same core (SMT sibling), same socket, or across sockets.
 * Comparable to `perf bench sched pipe`, but reports percentiles, not just the mean.
 *
 *   ./wakeup_latency [iterations=200000] [cpuA cpuB]
 * Output: p50/p90/p99/p999/max one-way latency in ns.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

static int ab[2], ba[2];
static long iters = 200000;
static int cpu_b = -1;

static void pin(int cpu) {
    if (cpu < 0) return;
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    pthread_setaffinity_np(pthread_self(), sizeof(set), &set);
}

static void *echo(void *arg) {
    (void)arg;
    pin(cpu_b);
    char c;
    for (long i = 0; i < iters; i++) {
        if (read(ab[0], &c, 1) != 1 || write(ba[1], &c, 1) != 1) break;
    }
    return NULL;
}

static int cmp(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

int main(int argc, char **argv) {
    if (argc > 1) iters = atol(argv[1]);
    int cpu_a = argc > 3 ? atoi(argv[2]) : -1;
    cpu_b = argc > 3 ? atoi(argv[3]) : -1;
    if (pipe(ab) || pipe(ba)) return 1;
    pin(cpu_a);
    pthread_t t;
    pthread_create(&t, NULL, echo, NULL);
    double *lat = malloc(sizeof(double) * iters);
    char c = 'x';
    for (long i = 0; i < iters; i++) {
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        if (write(ab[1], &c, 1) != 1 || read(ba[0], &c, 1) != 1) break;
        clock_gettime(CLOCK_MONOTONIC, &t1);
        lat[i] = ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec)) / 2; /* one-way */
    }
    pthread_join(t, NULL);
    qsort(lat, iters, sizeof(double), cmp);
    printf("cpus=%d,%d iterations=%ld\n", cpu_a, cpu_b, iters);
    printf("p50=%.0fns p90=%.0fns p99=%.0fns p999=%.0fns max=%.0fns\n", lat[iters / 2], lat[iters * 9 / 10], lat[iters * 99 / 100], lat[iters * 999 / 1000], lat[iters - 1]);
    free(lat);
    return 0;
}
