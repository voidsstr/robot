#include "RelayServer.h"

RelayServer::RelayServer(short clientListenPort, short robotListenPort)
{
    _service = new boost::asio::io_service();
    _acceptor = new tcp::acceptor(*_service, tcp::endpoint(tcp::v4(), robotListenPort));

    _clientSocket = new udp::socket(*_service, udp::endpoint(udp::v4(), clientListenPort));
    _robotSocket = new tcp::socket(*_service);

    ReceiveClientMessages();
    RecieveRobotConnections();
}

RelayServer::~RelayServer()
{
    //dtor
}

void RelayServer::ReceiveClientMessages()
{
    /*int buffer[2];

    _socket.async_receive_from(
        boost::asio::buffer(buffer, sizeof(buffer)), _clientEndpoint,
        [this](boost::system::error_code ec, std::size_t bytes_recvd)
        {
            if (!ec && bytes_recvd > 0)
            {
                std::cout << "Recieved data! ";
            }
            else
            {
                Receive();
            }
        });*/
}

void RelayServer::RelayMessageToRobot()
{
    /*_socket.async_send_to(
        boost::asio::buffer(_data, length), __clientEndpoint,
        [this](boost::system::error_code, std::size_t)
        {
            Receive();
        });*/
}

void RelayServer::RecieveRobotConnections()
{
    RobotConnection::pointer new_connection = RobotConnection::create(_acceptor->get_io_service());

    _acceptor->async_accept(new_connection->socket(), boost::bind(&RelayServer::HandleAccept, this, new_connection, boost::asio::placeholders::error));
}

void RelayServer::HandleAccept(RobotConnection::pointer new_connection, const boost::system::error_code& error)
{
    if (!error)
    {
        new_connection->start();
        RecieveRobotConnections();
    }
}

void RelayServer::Start()
{
    _service->run();
}
