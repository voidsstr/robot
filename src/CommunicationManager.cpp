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
    boost::asio::connect(s, resolver.resolve({ipAddress, port}));

    mvprintw(0, 0, "Enter message");

    //TODO: implement packet handling mechanism to interpret keystrokes

    char request[1024];
    std::cin.getline(request, 1024);
    size_t request_length = std::strlen(request);
    boost::asio::write(s, boost::asio::buffer(request, request_length));

    char reply[1024];
    size_t reply_length = boost::asio::read(s, boost::asio::buffer(reply, request_length));

    for(;;)
    {
        usleep(1000);
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


