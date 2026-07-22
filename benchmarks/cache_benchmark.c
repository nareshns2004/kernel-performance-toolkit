#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    size_t count = argc > 1 ? strtoull(argv[1], NULL, 10) : 8 * 1024 * 1024;
    uint64_t *data = calloc(count, sizeof(*data));
    if (!data) return 1;
    for (size_t i = 0; i < count; i += 16) data[i] += i;
    printf("value=%llu\n", (unsigned long long)data[count - 16]);
    free(data);
    return 0;
}
