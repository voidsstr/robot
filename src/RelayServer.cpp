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
    _clientSocket->async_receive_from(
        boost::asio::buffer(_data, sizeof(_data)), _clientEndpoint,
        [this](boost::system::error_code ec, std::size_t bytes_recvd)
        {
            if (!ec && bytes_recvd > 0)
            {
                std::cout << "Recieved data!\n";
                RelayMessageToRobot();
                ReceiveClientMessages();
            }
            else
            {
                ReceiveClientMessages();
            }
        });
}

void RelayServer::RelayMessageToRobot()
{
    std::cout << "Recieved message from client: \n";
    std::cout << _data[0] << "\n";
    std::cout << _data[1] << "\n";

    //_connections.at(0).send_message(_data);
}

void RelayServer::RecieveRobotConnections()
{
    RobotConnection* new_connection = new RobotConnection(_acceptor->get_io_service());

    _connections.push_back(new_connection);

    _acceptor->async_accept(new_connection->socket(), boost::bind(&RelayServer::HandleAccept, this, new_connection, boost::asio::placeholders::error));
}

void RelayServer::HandleAccept(RobotConnection* new_connection, const boost::system::error_code& error)
{
    if (!error)
    {
        new_connection->start();
        RecieveRobotConnections();
    }
}

void RelayServer::Start()
{
    std::cout << "Listening for robot and client...\n";
    _service->run();
}
