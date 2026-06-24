int g_plc_counter = 0;
double g_sensor_data[10];

void plc_cycle() {
    g_plc_counter++;
    void *p = malloc(10);
    printf("Counter: %d\n", g_plc_counter);
    free(p);
}
