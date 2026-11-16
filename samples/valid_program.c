#include <stdio.h>

int square(int n) {
    return n * n;
}

double average(int a, int b) {
    return (a + b) / 2.0;
}

int main() {
    int values[5] = {1, 2, 3, 4, 5};
    int i, total = 0;
    float ratio = 0.5f;
    char grade = 'A';

    for (i = 0; i < 5; i++) {
        if (values[i] % 2 == 0) {
            continue;
        }
        total += square(values[i]);
    }

    while (total > 100) {
        total -= 10;
    }

    do {
        total++;
    } while (total < 50);

    printf("Total: %d, average: %f, grade: %c\n", total, average(total, 7), grade);
    return 0;
}
