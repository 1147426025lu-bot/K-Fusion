int g_plc_counter = 0;
double g_sensor_data[5] = {25.5, 26.2, 27.1, 28.5, 30.0};
int g_heater_status = 0; // 0: OFF, 1: ON

void plc_cycle() {
    g_plc_counter++;
    
    // 模拟读取传感器数据（实际上是从隔离区数组读取）
    double current_temp = g_sensor_data[g_plc_counter % 5];
    
    // 逻辑控制：如果温度超过 28 度，关闭加热器；低于 26 度，开启
    if (current_temp > 28.0) {
        g_heater_status = 0;
    } else if (current_temp < 26.0) {
        g_heater_status = 1;
    }

    // 使用被改写的 printf (plc_printk) 输出状态
    if (g_heater_status) {
        printf("Cycle %d: Temp %.1f - HEATER ON\n", g_plc_counter, current_temp);
    } else {
        printf("Cycle %d: Temp %.1f - HEATER OFF\n", g_plc_counter, current_temp);
    }

    // 故意加一段动态内存操作，验证改写能力
    void *tmp = malloc(64);
    free(tmp);
}
