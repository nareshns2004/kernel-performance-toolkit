#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

/* Allocation placement is controlled externally (for example, numactl). */
int main(int argc, char **argv) {
    size_t count = argc > 1 ? strtoull(argv[1], NULL, 10) : 16 * 1024 * 1024;
    uint64_t *data = calloc(count, sizeof(*data));
    if (!data) return 1;
    for (size_t i = 0; i < count; i += 512) data[i] = i;
    printf("pages touched=%zu\n", count * sizeof(*data) / 4096);
    free(data);
    return 0;
}
