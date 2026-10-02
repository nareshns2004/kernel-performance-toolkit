/*
 * false_sharing: N threads increment their *own* counters, packed vs padded.
 *
 * Packed counters share 64-byte cache lines, so every increment invalidates the
 * line in the other cores' caches (MESI ping-pong), even though no data is
 * logically shared. Padding each counter to its own line removes the coherence
 * traffic. Expect a large slowdown for "packed" with >= 2 threads on different cores.
 *
 *   ./false_sharing [threads=4] [iterations=100000000]
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define MAX_THREADS 64
struct padded { _Alignas(64) volatile uint64_t v; char pad[64 - sizeof(uint64_t)]; };

static volatile uint64_t packed[MAX_THREADS];
static struct padded padded[MAX_THREADS];
static long iters = 100000000;

static void *bump_packed(void *arg) {
    long id = (long)arg;
    for (long i = 0; i < iters; i++) packed[id]++;
    return NULL;
}

static void *bump_padded(void *arg) {
    long id = (long)arg;
    for (long i = 0; i < iters; i++) padded[id].v++;
    return NULL;
}

static double run(void *(*fn)(void *), int n) {
    pthread_t t[MAX_THREADS];
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (long i = 0; i < n; i++) pthread_create(&t[i], NULL, fn, (void *)i);
    for (int i = 0; i < n; i++) pthread_join(t[i], NULL);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    return (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6;
}

int main(int argc, char **argv) {
    int n = argc > 1 ? atoi(argv[1]) : 4;
    if (n > MAX_THREADS) n = MAX_THREADS;
    if (argc > 2) iters = atol(argv[2]);
    double a = run(bump_packed, n), b = run(bump_padded, n);
    printf("threads=%d packed=%.1fms padded=%.1fms slowdown=%.1fx\n", n, a, b, a / b);
    return 0;
}
