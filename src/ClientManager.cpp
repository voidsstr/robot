#include "ClientManager.h"

ClientManager::ClientManager(char* ipAddress, int port)
{
    _ipAddress = ipAddress;
    _port = port;

    _ioService = new boost::asio::io_service;
    _socket = new udp::socket(*_ioService, udp::endpoint(udp::v4(), 0));

    char portString[4];
    sprintf(portString,"%d", _port);

    udp::resolver resolver(*_ioService);
    udp::resolver::query query(udp::v4(), _ipAddress, portString);
    udp::resolver::iterator iter = resolver.resolve(query);
    _relayServerEndpoint = *iter;
}

ClientManager::~ClientManager()
{
    //dtor
}

void ClientManager::SendMessage(int message[2])
{
    _socket->send_to(boost::asio::buffer(message, sizeof(message)), _relayServerEndpoint);
}
