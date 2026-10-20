CC := gcc
CFLAGS := -O3 -shared -fPIC -Wall -Wextra
TARGET := native/libpty_core.so
SRC := native/pty_core.c

.PHONY: all clean test run

all: $(TARGET)

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) $(SRC) -o $(TARGET) -lutil

clean:
	rm -f $(TARGET)

test: $(TARGET)
	.venv/bin/pytest -v

run: $(TARGET)
	.venv/bin/python app.py

