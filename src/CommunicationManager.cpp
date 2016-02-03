#include "CommunicationManager.h"

CommunicationManager::CommunicationManager()
{

}

CommunicationManager::~CommunicationManager()
{
    //dtor
}

/*Main communication loop*/
void ListenToNetworkCommands(char* ipAddress, char* port, CommunicationManager* communicationManager, NavigationCoordinator* navigationCoordinator)
{
    boost::asio::io_service io_service;

    tcp::socket s(io_service);
    tcp::resolver resolver(io_service);

    //Connect to server
    boost::asio::connect(s, resolver.resolve({ipAddress, port}));

    char reply[1024];

    for(;;)
    {
        //Read data
        size_t reply_length = boost::asio::read(s, boost::asio::buffer(reply));

        navigationCoordinator->UpdateNavigationParameters(DIRECTION::UP);
    }
}

void CommunicationManager::SendMessage(int message)
{
    boost::asio::write(*_socket, boost::asio::buffer("1"));
}

void CommunicationManager::Connect(char* ipAddress, char* port, NavigationCoordinator* navigationCoordinator)
{
    std::thread(ListenToNetworkCommands, ipAddress, port, this, navigationCoordinator).detach();
}

void CommunicationManager::StartListening()
{
    boost::asio::io_service io_service;

    tcp::acceptor a(io_service, tcp::endpoint(tcp::v4(), 1337));

    _socket = new tcp::socket(io_service);

    a.accept(*_socket);
}


