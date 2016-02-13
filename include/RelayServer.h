#ifndef RELAYSERVER_H
#define RELAYSERVER_H

#include <cstdlib>
#include <iostream>
#include <boost/asio.hpp>

#include <boost/array.hpp>
#include <boost/bind.hpp>
#include <boost/shared_ptr.hpp>
#include <boost/enable_shared_from_this.hpp>
#include <RobotConnection.h>

using boost::asio::ip::tcp;
using boost::asio::ip::udp;

class RelayServer
{
    public:
        RelayServer(short clientListenPort, short robotListenPort);
        virtual ~RelayServer();

        void Start();
    protected:
    private:
        int _data[2];
        udp::socket* _clientSocket;
        udp::endpoint _clientEndpoint;

        tcp::socket* _robotSocket;

        tcp::acceptor* _acceptor;
        boost::asio::io_service* _service;

        void RelayMessageToRobot();
        void ReceiveClientMessages();

        void RecieveRobotConnections();
        void HandleAccept(RobotConnection::pointer new_connection, const boost::system::error_code& error);
};

#endif // RELAYSERVER_H
