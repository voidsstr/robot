#include "CommunicationManager.h"

using boost::asio::ip::tcp;
using namespace boost::asio;

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

}

void CommunicationManager::Connect(char* ipAddress, char* port, NavigationCoordinator* navigationCoordinator)
{
    std::thread(ListenToNetworkCommands, ipAddress, port, this, navigationCoordinator).detach();
}

void session(tcp::socket sock)
{
    try
    {
        for (;;)
        {
            char data[10];

            boost::system::error_code error;

            size_t length = sock.read_some(boost::asio::buffer(data), error);

            if (error == boost::asio::error::eof)
            {
                break; // Connection closed cleanly by peer.
            }
            else if (error)
            {
                throw boost::system::system_error(error); // Some other error.
            }

            boost::asio::write(sock, boost::asio::buffer("1"));
        }
    }
    catch (std::exception& e)
    {
        std::cerr << "Exception in thread: " << e.what() << "\n";
    }
}

void server(boost::asio::io_service& io_service, unsigned short port)
{
    tcp::acceptor a(io_service, tcp::endpoint(tcp::v4(), port));

    for (;;)
    {
        tcp::socket sock(io_service);
        a.accept(sock);

        mvprintw(0, 0, "Recieved Data!");

        session(std::move(sock));
    }
}

void CommunicationManager::StartListening()
{
    boost::asio::io_service io_service;

    server(io_service, 1337);
}


