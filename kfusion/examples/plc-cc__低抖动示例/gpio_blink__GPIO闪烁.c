void plc_gpio_set(int pin, int value);
void plc_cycle(void) {
    static int count = 0;
    static int state = 0;
    count++;
    if (count % 500 == 0) {
        state = !state;
        plc_gpio_set(0, state);
    }
}
