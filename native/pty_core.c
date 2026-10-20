/*
 * native/pty_core.c
 * Núcleo nativo em C para gerenciamento de Pseudo-Terminal Linux (PTY),
 * filtragem rápida de sequências ANSI/VT100 e estimativa de tokens em O(N).
 *
 * Compilação:
 *   gcc -O3 -shared -fPIC -Wall -Wextra native/pty_core.c -o native/libpty_core.so -lutil
 */

#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pty.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

/*
 * Inicia um processo de shell real acoplado a um Pseudo-Terminal (PTY) Linux.
 * Retorna 0 em caso de sucesso, preenchendo out_master_fd e out_child_pid.
 */
int pty_spawn_shell(const char *shell_path, const char *initial_cwd, int rows, int cols,
                    int *out_master_fd, int *out_child_pid) {
    if (!out_master_fd || !out_child_pid) {
        return -1;
    }

    struct winsize ws;
    memset(&ws, 0, sizeof(ws));
    ws.ws_row = (rows > 0) ? (unsigned short)rows : 24;
    ws.ws_col = (cols > 0) ? (unsigned short)cols : 100;

    int master_fd = -1;
    pid_t pid = forkpty(&master_fd, NULL, NULL, &ws);
    if (pid < 0) {
        return -1;
    }

    if (pid == 0) {
        /* Processo filho: configura ambiente do terminal */
        if (initial_cwd && initial_cwd[0] != '\0') {
            if (chdir(initial_cwd) != 0) {
                /* Mantém diretório atual caso falhe */
            }
        }
        setenv("TERM", "dumb", 1);
        setenv("PS1", "", 1);
        setenv("PROMPT_COMMAND", "", 1);

        /* Desativa eco duplicado no slave PTY para leitura limpa */
        struct termios tios;
        if (tcgetattr(STDIN_FILENO, &tios) == 0) {
            tios.c_lflag &= ~(ECHO);
            tcsetattr(STDIN_FILENO, TCSANOW, &tios);
        }

        const char *sh = (shell_path && shell_path[0] != '\0') ? shell_path : "/bin/bash";
        execl(sh, sh, "--norc", "--noprofile", "-i", (char *)NULL);
        _exit(127);
    }

    /* Processo pai: configura descritor mestre como não-bloqueante */
    int flags = fcntl(master_fd, F_GETFL, 0);
    if (flags != -1) {
        fcntl(master_fd, F_SETFL, flags | O_NONBLOCK);
    }

    *out_master_fd = master_fd;
    *out_child_pid = (int)pid;
    return 0;
}

/*
 * Envia bytes (comandos ou entradas) para o descritor mestre do PTY.
 */
ssize_t pty_write_data(int master_fd, const char *buf, size_t count) {
    if (master_fd < 0 || !buf) {
        return -1;
    }
    size_t total_written = 0;
    while (total_written < count) {
        ssize_t w = write(master_fd, buf + total_written, count - total_written);
        if (w < 0) {
            if (errno == EINTR) {
                continue;
            }
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                break;
            }
            return -1;
        }
        total_written += (size_t)w;
    }
    return (ssize_t)total_written;
}

/*
 * Lê dados disponíveis do PTY de forma não-bloqueante usando poll().
 * Retorna o número de bytes lidos (0 se timeout, -1 se erro/EOF).
 */
ssize_t pty_read_nonblocking(int master_fd, char *out_buf, size_t capacity, int timeout_ms) {
    if (master_fd < 0 || !out_buf || capacity < 2) {
        return -1;
    }

    struct pollfd pfd;
    pfd.fd = master_fd;
    pfd.events = POLLIN;
    pfd.revents = 0;

    int ready = poll(&pfd, 1, timeout_ms);
    if (ready < 0) {
        if (errno == EINTR) {
            out_buf[0] = '\0';
            return 0;
        }
        return -1;
    }
    if (ready == 0 || !(pfd.revents & (POLLIN | POLLHUP))) {
        out_buf[0] = '\0';
        return 0;
    }

    ssize_t bytes_read = read(master_fd, out_buf, capacity - 1);
    if (bytes_read < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
            out_buf[0] = '\0';
            return 0;
        }
        return -1;
    }
    out_buf[bytes_read] = '\0';
    return bytes_read;
}

/*
 * Máquina de estados em C para limpar sequências de escape ANSI/VT100
 * (CSI \x1b[..., OSC \x1b]..., \r) deixando o texto puro para renderização.
 */
size_t pty_strip_ansi(const char *input, size_t input_len, char *output, size_t out_capacity) {
    if (!input || !output || out_capacity == 0) {
        return 0;
    }

    size_t out_idx = 0;
    size_t i = 0;

    while (i < input_len && out_idx + 1 < out_capacity) {
        unsigned char ch = (unsigned char)input[i];

        if (ch == '\r') {
            i++;
            continue;
        }

        if (ch == 0x1B) { /* ESC */
            i++;
            if (i >= input_len) {
                break;
            }
            unsigned char next = (unsigned char)input[i];
            if (next == '[') {
                /* Sequência CSI: consome até encontrar byte entre 0x40 e 0x7E */
                i++;
                while (i < input_len) {
                    unsigned char c = (unsigned char)input[i++];
                    if (c >= 0x40 && c <= 0x7E) {
                        break;
                    }
                }
            } else if (next == ']') {
                /* Sequência OSC: consome até BEL (0x07) ou ST (ESC \) */
                i++;
                while (i < input_len) {
                    unsigned char c = (unsigned char)input[i++];
                    if (c == 0x07) {
                        break;
                    }
                    if (c == 0x1B && i < input_len && input[i] == '\\') {
                        i++;
                        break;
                    }
                }
            } else {
                /* Outra sequência de 2 bytes */
                i++;
            }
            continue;
        }

        /* Ignora caracteres de controle não-imprimíveis exceto \n e \t */
        if (ch < 0x20 && ch != '\n' && ch != '\t') {
            i++;
            continue;
        }

        output[out_idx++] = (char)ch;
        i++;
    }

    output[out_idx] = '\0';
    return out_idx;
}

/*
 * Inspeciona /proc/<pid>/cwd diretamente via syscall readlink para obter
 * o diretório de trabalho atual real do shell em tempo constante.
 */
int pty_get_cwd(int child_pid, char *out_buf, size_t capacity) {
    if (child_pid <= 0 || !out_buf || capacity < 2) {
        return -1;
    }
    char proc_path[64];
    snprintf(proc_path, sizeof(proc_path), "/proc/%d/cwd", child_pid);
    ssize_t len = readlink(proc_path, out_buf, capacity - 1);
    if (len < 0) {
        return -1;
    }
    out_buf[len] = '\0';
    return 0;
}

/*
 * Envia um sinal POSIX (ex: SIGINT = 2, SIGTERM = 15) ao grupo de processos do PTY.
 */
int pty_send_signal(int child_pid, int sig) {
    if (child_pid <= 0) {
        return -1;
    }
    if (kill(-child_pid, sig) == 0) {
        return 0;
    }
    return kill(child_pid, sig);
}

/*
 * Encerra a sessão PTY, fechando o descritor mestre e aguardando o processo filho.
 */
int pty_close_session(int master_fd, int child_pid) {
    if (master_fd >= 0) {
        close(master_fd);
    }
    if (child_pid > 0) {
        kill(child_pid, SIGHUP);
        kill(child_pid, SIGTERM);
        int status = 0;
        waitpid((pid_t)child_pid, &status, WNOHANG);
    }
    return 0;
}

/*
 * Estimador nativo ultrarrápido de tokens para LLMs em passagem única O(N).
 * Combina contagem de fronteiras de palavras/símbolos e heurística de caracteres.
 */
int fast_estimate_tokens(const char *text, size_t len) {
    if (!text || len == 0) {
        return 0;
    }

    size_t chunks = 0;
    int in_word = 0;
    size_t non_space_chars = 0;

    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)text[i];
        if (isspace(c)) {
            in_word = 0;
        } else {
            non_space_chars++;
            if (isalnum(c) || c == '_' || c >= 0x80) {
                if (!in_word) {
                    chunks++;
                    in_word = 1;
                }
            } else {
                /* Pontuação / operadores contam como tokens individuais */
                chunks++;
                in_word = 0;
            }
        }
    }

    if (non_space_chars == 0) {
        return 0;
    }

    int char_est = (int)((len + 2) / 4);
    if (char_est < 1) {
        char_est = 1;
    }
    int word_est = (int)(chunks * 1.15);
    if (word_est < 1) {
        word_est = 1;
    }
    int avg = (char_est + word_est + 1) / 2;
    return (avg > 0) ? avg : 1;
}

