#ifndef CLIENTMANAGER_H
#define CLIENTMANAGER_H

// ncurses (pulled in via other headers) defines a `timeout` macro that
// collides with boost::asio::basic_socket_streambuf::timeout(). Drop it
// before including Asio.
#ifdef timeout
#undef timeout
#endif
#include <boost/asio.hpp>

using boost::asio::ip::udp;

class ClientManager
{
public:
    ClientManager(char* ipAddress, int port);
    virtual ~ClientManager();

    void SendMessage(int message[2]);
protected:
private:
    char* _ipAddress;
    int _port;
    udp::endpoint _relayServerEndpoint;
    udp::socket* _socket;
    boost::asio::io_service* _ioService;
};

#endif // CLIENTMANAGER_H
