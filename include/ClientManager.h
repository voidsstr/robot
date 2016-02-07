#ifndef CLIENTMANAGER_H
#define CLIENTMANAGER_H

#include <boost/asio.hpp>

using boost::asio::ip::udp;

class ClientManager
{
    public:
        ClientManager(char* ipAddress, int port);
        virtual ~ClientManager();

        void SendMessage(int* message);
    protected:
    private:
        char* _ipAddress;
        int _port;
        udp::endpoint _relayServerEndpoint;
        udp::socket* _socket;
        boost::asio::io_service* _ioService;
};

#endif // CLIENTMANAGER_H
