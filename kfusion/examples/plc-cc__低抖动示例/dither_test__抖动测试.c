void plc_logic() {
    void* ptr = malloc(1024); // 危险！
    printf("Processing...\n"); // 危险！
    free(ptr);
}
