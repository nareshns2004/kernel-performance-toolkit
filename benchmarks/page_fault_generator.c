#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    size_t pages = argc > 1 ? strtoull(argv[1], NULL, 10) : 4096;
    long page_size = sysconf(_SC_PAGESIZE);
    char *memory = malloc(pages * (size_t)page_size);
    if (!memory) return 1;
    for (size_t i = 0; i < pages; ++i) memory[i * (size_t)page_size] = (char)i;
    printf("touched=%zu pages\n", pages);
    free(memory);
    return 0;
}
