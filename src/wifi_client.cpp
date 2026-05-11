#include <iostream>
#include <cstring>
#include <unistd.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <curses.h>
#include <thread>
#include <atomic>
#include <mutex>
#include <queue>
#include <algorithm>
#include <string>

class RobotWifiClient
{
public:
    RobotWifiClient() : _socket(-1), _connected(false), _running(false) {}

    ~RobotWifiClient()
    {
        disconnect();
    }

    bool connect(const std::string& host, int port)
    {
        _socket = socket(AF_INET, SOCK_STREAM, 0);
        if (_socket < 0)
        {
            return false;
        }

        struct hostent* server = gethostbyname(host.c_str());
        if (server == nullptr)
        {
            close(_socket);
            return false;
        }

        struct sockaddr_in serverAddr;
        memset(&serverAddr, 0, sizeof(serverAddr));
        serverAddr.sin_family = AF_INET;
        memcpy(&serverAddr.sin_addr.s_addr, server->h_addr, server->h_length);
        serverAddr.sin_port = htons(port);

        if (::connect(_socket, (struct sockaddr*)&serverAddr, sizeof(serverAddr)) < 0)
        {
            close(_socket);
            return false;
        }

        _connected = true;
        _running = true;
        _host = host;
        _port = port;

        // Start receiver thread
        _receiverThread = std::thread(&RobotWifiClient::receiverLoop, this);

        return true;
    }

    void disconnect()
    {
        _running = false;
        _connected = false;

        if (_socket >= 0)
        {
            shutdown(_socket, SHUT_RDWR);
            close(_socket);
            _socket = -1;
        }

        if (_receiverThread.joinable())
        {
            _receiverThread.join();
        }
    }

    bool sendCommand(const std::string& cmd)
    {
        if (!_connected) return false;

        std::string data = cmd + "\n";
        return send(_socket, data.c_str(), data.length(), 0) > 0;
    }

    bool hasMessage()
    {
        std::lock_guard<std::mutex> lock(_msgMutex);
        return !_messages.empty();
    }

    std::string getMessage()
    {
        std::lock_guard<std::mutex> lock(_msgMutex);
        if (_messages.empty()) return "";
        std::string msg = _messages.front();
        _messages.pop();
        return msg;
    }

    bool isConnected() const { return _connected; }
    std::string getHost() const { return _host; }
    int getPort() const { return _port; }

private:
    int _socket;
    bool _connected;
    std::atomic<bool> _running;
    std::string _host;
    int _port;
    std::thread _receiverThread;
    std::mutex _msgMutex;
    std::queue<std::string> _messages;

    void receiverLoop()
    {
        char buffer[512];

        while (_running && _connected)
        {
            struct timeval tv;
            tv.tv_sec = 0;
            tv.tv_usec = 100000;  // 100ms
            setsockopt(_socket, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

            memset(buffer, 0, sizeof(buffer));
            ssize_t bytesRead = recv(_socket, buffer, sizeof(buffer) - 1, 0);

            if (bytesRead > 0)
            {
                std::lock_guard<std::mutex> lock(_msgMutex);
                _messages.push(std::string(buffer));
            }
            else if (bytesRead == 0)
            {
                _connected = false;
                break;
            }
        }
    }
};

// ASCII Art Robot Display
void drawRobot(WINDOW* win, int speed, const std::string& direction)
{
    int startY = 2;
    int startX = 2;

    // Clear area
    for (int i = 0; i < 12; i++)
    {
        mvwprintw(win, startY + i, startX, "                              ");
    }

    // Draw robot
    mvwprintw(win, startY + 0, startX, "        ____");
    mvwprintw(win, startY + 1, startX, "       /    \\");
    mvwprintw(win, startY + 2, startX, "      | (@@) |    <- sensors");
    mvwprintw(win, startY + 3, startX, "       \\____/");
    mvwprintw(win, startY + 4, startX, "      __|  |__");
    mvwprintw(win, startY + 5, startX, "     |        |");
    mvwprintw(win, startY + 6, startX, "     | ROBOT  |");
    mvwprintw(win, startY + 7, startX, "     |________|");
    mvwprintw(win, startY + 8, startX, "      O      O   <- wheels");

    // Draw direction indicator
    if (direction == "UP" || direction == "FORWARD")
    {
        mvwprintw(win, startY - 1, startX + 8, "^");
        mvwprintw(win, startY - 0, startX + 8, "|");
    }
    else if (direction == "DOWN" || direction == "BACK")
    {
        mvwprintw(win, startY + 9, startX + 8, "|");
        mvwprintw(win, startY + 10, startX + 8, "v");
    }
    else if (direction == "LEFT")
    {
        mvwprintw(win, startY + 6, startX - 3, "<--");
    }
    else if (direction == "RIGHT")
    {
        mvwprintw(win, startY + 6, startX + 15, "-->");
    }
    else if (direction == "STOP")
    {
        mvwprintw(win, startY + 10, startX + 4, "[STOPPED]");
    }

    wrefresh(win);
}

void drawUI(WINDOW* mainWin, WINDOW* statusWin, WINDOW* logWin,
            RobotWifiClient& client, int speed, const std::string& lastCmd)
{
    // Main window - robot display
    box(mainWin, 0, 0);
    mvwprintw(mainWin, 0, 2, " ROBOT VIEW ");
    drawRobot(mainWin, speed, lastCmd);
    wrefresh(mainWin);

    // Status window
    box(statusWin, 0, 0);
    mvwprintw(statusWin, 0, 2, " STATUS ");
    mvwprintw(statusWin, 1, 2, "Connected: %s", client.isConnected() ? "YES" : "NO ");
    mvwprintw(statusWin, 2, 2, "Host: %s:%d", client.getHost().c_str(), client.getPort());
    mvwprintw(statusWin, 3, 2, "Speed: %d     ", speed);
    mvwprintw(statusWin, 4, 2, "Last Cmd: %-10s", lastCmd.c_str());
    wrefresh(statusWin);

    // Log window header
    box(logWin, 0, 0);
    mvwprintw(logWin, 0, 2, " SERVER LOG ");
    wrefresh(logWin);
}

void printUsage(const char* programName)
{
    std::cout << "Robot WiFi Client - ASCII Control Interface" << std::endl;
    std::cout << std::endl;
    std::cout << "Usage: " << programName << " <host> [port]" << std::endl;
    std::cout << std::endl;
    std::cout << "  host    Robot IP address or hostname" << std::endl;
    std::cout << "  port    TCP port (default: 8080)" << std::endl;
    std::cout << std::endl;
    std::cout << "Example: " << programName << " 192.168.1.100 8080" << std::endl;
}

int main(int argc, char* argv[])
{
    if (argc < 2)
    {
        printUsage(argv[0]);
        return 1;
    }

    std::string host = argv[1];
    int port = (argc >= 3) ? atoi(argv[2]) : 8080;

    // Initialize curses
    initscr();
    cbreak();
    noecho();
    keypad(stdscr, TRUE);
    nodelay(stdscr, TRUE);
    curs_set(0);

    // Create windows
    int maxY, maxX;
    getmaxyx(stdscr, maxY, maxX);

    WINDOW* mainWin = newwin(15, 35, 0, 0);
    WINDOW* statusWin = newwin(7, 35, 0, 36);
    WINDOW* logWin = newwin(7, 35, 8, 36);
    WINDOW* helpWin = newwin(6, maxX, 16, 0);

    // Draw help
    box(helpWin, 0, 0);
    mvwprintw(helpWin, 0, 2, " CONTROLS ");
    mvwprintw(helpWin, 1, 2, "Arrow Keys: Move    Space: Stop    Q: Quit");
    mvwprintw(helpWin, 2, 2, "W/A/S/D:    Move    X: Emergency Stop");
    mvwprintw(helpWin, 3, 2, "?: Query status from robot");
    wrefresh(helpWin);

    // Connect to robot
    RobotWifiClient client;

    mvprintw(maxY - 1, 0, "Connecting to %s:%d...", host.c_str(), port);
    refresh();

    if (!client.connect(host, port))
    {
        endwin();
        std::cerr << "Failed to connect to " << host << ":" << port << std::endl;
        return 1;
    }

    mvprintw(maxY - 1, 0, "Connected! Use arrow keys to control robot.%s", "          ");
    refresh();

    int speed = 0;
    std::string lastCmd = "NONE";
    int logLine = 1;

    // Main loop
    bool running = true;
    while (running && client.isConnected())
    {
        // Check for server messages
        while (client.hasMessage())
        {
            std::string msg = client.getMessage();

            // Remove newlines for display
            msg.erase(std::remove(msg.begin(), msg.end(), '\n'), msg.end());
            msg.erase(std::remove(msg.begin(), msg.end(), '\r'), msg.end());

            // Display in log window (scroll if needed)
            if (logLine >= 6)
            {
                logLine = 1;
                werase(logWin);
            }
            mvwprintw(logWin, logLine++, 1, "%-33s", msg.substr(0, 33).c_str());
            box(logWin, 0, 0);
            mvwprintw(logWin, 0, 2, " SERVER LOG ");
            wrefresh(logWin);
        }

        // Process input
        int ch = getch();
        std::string cmd = "";

        switch (ch)
        {
            case KEY_UP:
            case 'w':
            case 'W':
                cmd = "UP";
                speed++;
                break;

            case KEY_DOWN:
            case 's':
            case 'S':
                cmd = "DOWN";
                speed--;
                break;

            case KEY_LEFT:
            case 'a':
            case 'A':
                cmd = "LEFT";
                break;

            case KEY_RIGHT:
            case 'd':
            case 'D':
                cmd = "RIGHT";
                break;

            case ' ':
            case 'x':
            case 'X':
                cmd = "STOP";
                speed = 0;
                break;

            case '?':
                cmd = "STATUS";
                break;

            case 'q':
            case 'Q':
                running = false;
                break;

            default:
                break;
        }

        if (!cmd.empty() && cmd != "STATUS")
        {
            client.sendCommand(cmd);
            lastCmd = cmd;
        }
        else if (cmd == "STATUS")
        {
            client.sendCommand(cmd);
        }

        // Update display
        drawUI(mainWin, statusWin, logWin, client, speed, lastCmd);

        usleep(50000);  // 50ms
    }

    // Cleanup
    client.disconnect();

    delwin(mainWin);
    delwin(statusWin);
    delwin(logWin);
    delwin(helpWin);
    endwin();

    std::cout << "Disconnected from robot." << std::endl;

    return 0;
}
