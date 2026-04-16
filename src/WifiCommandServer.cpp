#include "WifiCommandServer.h"

#include <iostream>
#include <cstring>
#include <unistd.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <algorithm>

WifiCommandServer::WifiCommandServer(int port)
    : _port(port), _serverSocket(-1), _running(false), _navCoordinator(nullptr)
{
}

WifiCommandServer::~WifiCommandServer()
{
    stop();
}

bool WifiCommandServer::start(NavigationCoordinator* navCoordinator)
{
    _navCoordinator = navCoordinator;

    // Create socket
    _serverSocket = socket(AF_INET, SOCK_STREAM, 0);
    if (_serverSocket < 0)
    {
        std::cerr << "Failed to create socket" << std::endl;
        return false;
    }

    // Allow socket reuse
    int opt = 1;
    setsockopt(_serverSocket, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    // Bind to port
    struct sockaddr_in serverAddr;
    memset(&serverAddr, 0, sizeof(serverAddr));
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_addr.s_addr = INADDR_ANY;
    serverAddr.sin_port = htons(_port);

    if (bind(_serverSocket, (struct sockaddr*)&serverAddr, sizeof(serverAddr)) < 0)
    {
        std::cerr << "Failed to bind to port " << _port << std::endl;
        close(_serverSocket);
        return false;
    }

    // Listen for connections
    if (listen(_serverSocket, 5) < 0)
    {
        std::cerr << "Failed to listen on socket" << std::endl;
        close(_serverSocket);
        return false;
    }

    _running = true;

    // Start server thread
    _serverThread = std::thread(&WifiCommandServer::serverLoop, this);

    std::cout << "WiFi Command Server started on port " << _port << std::endl;
    return true;
}

void WifiCommandServer::stop()
{
    _running = false;

    if (_serverSocket >= 0)
    {
        shutdown(_serverSocket, SHUT_RDWR);
        close(_serverSocket);
        _serverSocket = -1;
    }

    if (_serverThread.joinable())
    {
        _serverThread.join();
    }
}

bool WifiCommandServer::isRunning() const
{
    return _running;
}

void WifiCommandServer::serverLoop()
{
    while (_running)
    {
        struct sockaddr_in clientAddr;
        socklen_t clientLen = sizeof(clientAddr);

        // Accept connection (with timeout)
        struct timeval tv;
        tv.tv_sec = 1;
        tv.tv_usec = 0;
        setsockopt(_serverSocket, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

        int clientSocket = accept(_serverSocket, (struct sockaddr*)&clientAddr, &clientLen);

        if (clientSocket < 0)
        {
            continue;  // Timeout or error, check if still running
        }

        // Get client IP
        char clientIP[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &clientAddr.sin_addr, clientIP, INET_ADDRSTRLEN);
        _lastClientIP = std::string(clientIP);

        std::cout << "Client connected: " << _lastClientIP << std::endl;

        // Handle client in this thread (single client at a time)
        handleClient(clientSocket, _lastClientIP);

        close(clientSocket);
        std::cout << "Client disconnected: " << _lastClientIP << std::endl;
    }
}

void WifiCommandServer::handleClient(int clientSocket, const std::string& clientIP)
{
    char buffer[256];

    // Send welcome message
    const char* welcome = "ROBOT READY\nCommands: UP, DOWN, LEFT, RIGHT, STOP, STATUS, QUIT\n";
    send(clientSocket, welcome, strlen(welcome), 0);

    while (_running)
    {
        memset(buffer, 0, sizeof(buffer));

        // Receive with timeout
        struct timeval tv;
        tv.tv_sec = 1;
        tv.tv_usec = 0;
        setsockopt(clientSocket, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

        ssize_t bytesRead = recv(clientSocket, buffer, sizeof(buffer) - 1, 0);

        if (bytesRead <= 0)
        {
            if (bytesRead == 0)
            {
                break;  // Client disconnected
            }
            continue;  // Timeout, check if still running
        }

        // Parse command
        std::string cmd(buffer);

        // Remove whitespace
        cmd.erase(std::remove(cmd.begin(), cmd.end(), '\n'), cmd.end());
        cmd.erase(std::remove(cmd.begin(), cmd.end(), '\r'), cmd.end());

        // Convert to uppercase
        std::transform(cmd.begin(), cmd.end(), cmd.begin(), ::toupper);

        if (cmd == "QUIT" || cmd == "EXIT")
        {
            send(clientSocket, "BYE\n", 4, 0);
            break;
        }

        if (cmd == "STATUS")
        {
            std::string status = "OK: Connected to Arduino: ";
            status += (_navCoordinator->IsConnected() ? "YES" : "NO");
            status += "\n";
            send(clientSocket, status.c_str(), status.length(), 0);
            continue;
        }

        DIRECTION direction = parseCommand(cmd);

        if (direction != DIRECTION::UNKNOWN)
        {
            _navCoordinator->UpdateNavigationParameters(direction);
            _navCoordinator->ProcessUpdate();

            std::string response = "OK: " + cmd + "\n";
            send(clientSocket, response.c_str(), response.length(), 0);
        }
        else
        {
            send(clientSocket, "ERR: Unknown command\n", 21, 0);
        }
    }
}

DIRECTION WifiCommandServer::parseCommand(const std::string& cmd)
{
    if (cmd == "UP" || cmd == "FORWARD" || cmd == "W" || cmd == "ACC" || cmd == "ACCELERATE")
    {
        return DIRECTION::UP;
    }
    else if (cmd == "DOWN" || cmd == "BACK" || cmd == "S" || cmd == "DEC" || cmd == "DECELERATE")
    {
        return DIRECTION::DOWN;
    }
    else if (cmd == "LEFT" || cmd == "A")
    {
        return DIRECTION::LEFT;
    }
    else if (cmd == "RIGHT" || cmd == "D")
    {
        return DIRECTION::RIGHT;
    }
    else if (cmd == "STOP" || cmd == "SPACE" || cmd == "X")
    {
        return DIRECTION::STOP;
    }

    return DIRECTION::UNKNOWN;
}
