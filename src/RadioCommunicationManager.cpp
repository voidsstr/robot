#include "RadioCommunicationManager.h"

RadioCommunicationManager::RadioCommunicationManager(enum TransmissionMode mode)
{
    m_strRadioIdentifier = "radio://0/10/250K";
    m_enumPower = P_M18DBM;

    m_ctxContext = NULL;
    m_hndlDevice = NULL;

    m_bAckReceived = false;
    m_enumTransmissionMode = mode;

    libusb_init(&m_ctxContext);
}

RadioCommunicationManager::~RadioCommunicationManager()
{
    //dtor
}

bool RadioCommunicationManager::startRadio()
{
    if(this->openUSBDongle())
    {
        int nDongleNBR;
        int nRadioChannel;
        int nDataRate;
        char cDataRateType;

        if(std::sscanf(m_strRadioIdentifier.c_str(), "radio://%d/%d/%d%c", &nDongleNBR, &nRadioChannel, &nDataRate, &cDataRateType) != EOF)
        {
            std::stringstream message;
            message << "Opening radio " << nDongleNBR << "/" << nRadioChannel << "/" << nDataRate << std::to_string(cDataRateType);

            HUDManager::logMessage(HardwareStatus, message.str());

            std::stringstream sts;
            sts << nDataRate;
            sts << cDataRateType;
            std::string strDataRate = sts.str();

            // Read device version
            libusb_device_descriptor ddDescriptor;
            libusb_get_device_descriptor(m_devDevice, &ddDescriptor);
            sts.clear();
            sts.str(std::string());
            sts << (ddDescriptor.bcdDevice >> 8);
            sts << ".";
            sts << (ddDescriptor.bcdDevice & 0x0ff);
            std::sscanf(sts.str().c_str(), "%f", &m_fDeviceVersion);

            std::stringstream hardwareMessage;
            hardwareMessage << "Radio firmware version: " << m_fDeviceVersion;

            HUDManager::logMessage(HardwareStatus, hardwareMessage.str());

            if(m_fDeviceVersion < 0.3)
            {
                return false;
            }

            // Set active configuration to 1
            libusb_set_configuration(m_hndlDevice, 1);

            // Claim interface
            if(this->claimInterface(0))
            {
                // Set power-up settings for dongle (>= v0.4)
                this->setDataRate("2M");
                this->setChannel(2);
                this->setTransmissionMode(m_enumTransmissionMode);

                if(m_fDeviceVersion >= 0.4) {
                    this->setContCarrier(false);
                    char cAddress[5];
                    cAddress[0] = 0xe7;
                    cAddress[1] = 0xe7;
                    cAddress[2] = 0xe7;
                    cAddress[3] = 0xe7;
                    cAddress[4] = 0xe7;
                    this->setAddress(cAddress);
                    this->setPower(P_0DBM);
                    this->setARC(3);
                    this->setARDBytes(32);
                }

                // Initialize device
                if(m_fDeviceVersion >= 0.4) {
                    this->setARC(10);
                }

                this->setChannel(nRadioChannel);
                this->setDataRate(strDataRate);

                return true;
            }
        }
    }

    return false;
}

std::list<libusb_device*> RadioCommunicationManager::listDevices(int nVendorID, int nProductID)
{
    std::list<libusb_device*> lstDevices;
    ssize_t szCount;
    libusb_device **ptDevices;

    szCount = libusb_get_device_list(m_ctxContext, &ptDevices);

    for(unsigned int unI = 0; unI < szCount; unI++)
    {
        libusb_device *devCurrent = ptDevices[unI];
        libusb_device_descriptor ddDescriptor;

        libusb_get_device_descriptor(devCurrent, &ddDescriptor);

        if(ddDescriptor.idVendor == nVendorID && ddDescriptor.idProduct == nProductID) {
          libusb_ref_device(devCurrent);
          lstDevices.push_back(devCurrent);
        }
    }

    if(szCount > 0)
    {
        libusb_free_device_list(ptDevices, 1);
    }

    return lstDevices;
}

void RadioCommunicationManager::closeDevice()
{
    if(m_hndlDevice)
    {
        libusb_close(m_hndlDevice);
        libusb_unref_device(m_devDevice);

        m_hndlDevice = NULL;
        m_devDevice = NULL;
    }
}

bool RadioCommunicationManager::openUSBDongle()
{
    //this->closeDevice();
    std::list<libusb_device*> lstDevices = this->listDevices(0x1915, 0x7777);

    if(lstDevices.size() > 0)
    {
        // For now, just take the first device. Give it a second to
        // initialize the system permissions.
        sleep(1.0);

        libusb_device *devFirst = lstDevices.front();
        int nError = libusb_open(devFirst, &m_hndlDevice);

        if(nError == 0)
        {
            // Opening device OK. Don't free the first device just yet.
            lstDevices.pop_front();
            m_devDevice = devFirst;
        }

        for(std::list<libusb_device*>::iterator itDevice = lstDevices.begin();
        itDevice != lstDevices.end();
        itDevice++)
        {
            libusb_device *devCurrent = *itDevice;
            libusb_unref_device(devCurrent);
        }

        return !nError;
    }

    return false;
}

CCRTPPacket *RadioCommunicationManager::writeData(void *vdData, int nLength)
{
    CCRTPPacket *crtpPacket = NULL;

    int nActuallyWritten;
    int nReturn = libusb_bulk_transfer(m_hndlDevice, (0x01 | LIBUSB_ENDPOINT_OUT), (unsigned char*)vdData, nLength, &nActuallyWritten, 1000);

    if(nReturn == 0 && nActuallyWritten == nLength)
    {
        crtpPacket = this->readACK();
    }

    return crtpPacket;
}

bool RadioCommunicationManager::readData(void *vdData, int &nMaxLength)
{
    int nActuallyRead;
    int nReturn = libusb_bulk_transfer(m_hndlDevice, (0x81 | LIBUSB_ENDPOINT_IN), (unsigned char*)vdData, nMaxLength, &nActuallyRead, 50);

    if(nReturn == 0)
    {
        nMaxLength = nActuallyRead;

        return true;
    }
    else
    {
        switch(nReturn)
        {
            case LIBUSB_ERROR_TIMEOUT:
              mvprintw(0, 3, "USB Read Timeout\n");
              break;

            default:
              break;
        }
    }

    return false;
}

bool RadioCommunicationManager::writeControl(void *vdData, int nLength, uint8_t u8Request, uint16_t u16Value, uint16_t u16Index)
{
    int nTimeout = 1000;

    libusb_control_transfer(m_hndlDevice, LIBUSB_REQUEST_TYPE_VENDOR, u8Request, u16Value, u16Index, (unsigned char*)vdData, nLength, nTimeout);

    // if(nReturn == 0) {
    //   return true;
    // }

    // Hack.
    return true;
}

void RadioCommunicationManager::setARC(int nARC)
{
    m_nARC = nARC;
    this->writeControl(NULL, 0, 0x06, nARC, 0);
}

void RadioCommunicationManager::setChannel(int nChannel)
{
    m_nChannel = nChannel;
    this->writeControl(NULL, 0, 0x01, nChannel, 0);
}

void RadioCommunicationManager::setDataRate(std::string strDataRate)
{
    m_strDataRate = strDataRate;
    int nDataRate = -1;

    if(m_strDataRate == "250K")
    {
        nDataRate = 0;
    }
    else if(m_strDataRate == "1M")
    {
        nDataRate = 1;
    }
    else if(m_strDataRate == "2M")
    {
        nDataRate = 2;
    }

    this->writeControl(NULL, 0, 0x03, nDataRate, 0);
}

void RadioCommunicationManager::setARDTime(int nARDTime)
{ // in uSec
    m_nARDTime = nARDTime;

    int nT = int((nARDTime / 250) - 1);

    if(nT < 0)
    {
        nT = 0;
    } else if(nT > 0xf)
    {
        nT = 0xf;
    }

    this->writeControl(NULL, 0, 0x05, nT, 0);
}

void RadioCommunicationManager::setARDBytes(int nARDBytes)
{
    m_nARDBytes = nARDBytes;

    this->writeControl(NULL, 0, 0x05, 0x80 | nARDBytes, 0);
}

enum Power RadioCommunicationManager::power()
{
    return m_enumPower;
}

void RadioCommunicationManager::setPower(enum Power enumPower)
{
    m_enumPower = enumPower;

    this->writeControl(NULL, 0, 0x04, enumPower, 0);
}

void RadioCommunicationManager::setTransmissionMode(enum TransmissionMode transmissionMode)
{
    m_enumTransmissionMode = transmissionMode;

    this->writeControl(NULL, 0, 0x22, transmissionMode, 0);
}

void RadioCommunicationManager::setAddress(char *cAddress)
{
    m_cAddress = cAddress;

    this->writeControl(cAddress, 5, 0x02, 0, 0);
}

void RadioCommunicationManager::setContCarrier(bool bContCarrier)
{
    m_bContCarrier = bContCarrier;

    this->writeControl(NULL, 0, 0x20, (bContCarrier ? 1 : 0), 0);
}

bool RadioCommunicationManager::claimInterface(int nInterface)
{
    return libusb_claim_interface(m_hndlDevice, nInterface) == 0;
}

CCRTPPacket *RadioCommunicationManager::sendPacket(CCRTPPacket *crtpSend, bool bDeleteAfterwards)
{
    CCRTPPacket *crtpPacket = NULL;

    crtpPacket = this->writeData(crtpSend->data(), crtpSend->dataLength());

    if(bDeleteAfterwards)
    {
        delete crtpSend;
    }

    return crtpPacket;
}

CCRTPPacket *RadioCommunicationManager::readACK()
{
    CCRTPPacket *crtpPacket = NULL;

    int nBufferSize = 64;
    char cBuffer[nBufferSize];
    int nBytesRead = nBufferSize;

    if(this->readData(cBuffer, nBytesRead))
    {
        if(nBytesRead > 0)
        {
            m_bAckReceived = true;

            crtpPacket = new CCRTPPacket(0);

            if(nBytesRead > 1)
            {
                crtpPacket->setData(&cBuffer[1], nBytesRead);
            }
        }
        else
        {
            m_bAckReceived = false;
        }
    }

    return crtpPacket;
}

bool RadioCommunicationManager::ackReceived()
{
    return m_bAckReceived;
}

bool RadioCommunicationManager::usbOK()
{
    libusb_device_descriptor ddDescriptor;
    return (libusb_get_device_descriptor(m_devDevice, &ddDescriptor) == 0);
}

CCRTPPacket *RadioCommunicationManager::waitForPacket()
{
    bool bGoon = true;
    CCRTPPacket *crtpReceived = NULL;
    char* command = "dfg";
    CCRTPPacket* crtpDummy = new CCRTPPacket(command, sizeof(command), 1);
    crtpDummy->setIsPingPacket(true);

    while(bGoon)
    {
        crtpReceived = this->sendPacket(crtpDummy);
        bGoon = (crtpReceived == NULL);
    }

    delete crtpDummy;
    return crtpReceived;
}

CCRTPPacket *RadioCommunicationManager::sendAndReceive(CCRTPPacket *crtpSend, bool bDeleteAfterwards)
{
    return this->sendAndReceive(crtpSend, crtpSend->port(), crtpSend->channel(), bDeleteAfterwards);
}

CCRTPPacket *RadioCommunicationManager::sendAndReceive(CCRTPPacket *crtpSend, int nPort, int nChannel, bool bDeleteAfterwards, int nRetries, int nMicrosecondsWait)
{
    bool bGoon = true;
    int nResendCounter = 0;
    CCRTPPacket *crtpReturnvalue = NULL;
    CCRTPPacket *crtpReceived = NULL;

    while(bGoon)
    {
        if(nResendCounter == 0)
        {
            crtpReceived = this->sendPacket(crtpSend);
            nResendCounter = nRetries;
        }
        else
        {
            nResendCounter--;
        }

        if(crtpReceived)
        {
            if(crtpReceived->port() == nPort && crtpReceived->channel() == nChannel)
            {
                crtpReturnvalue = crtpReceived;
                bGoon = false;
            }
        }

        if(bGoon)
        {
            if(crtpReceived)
            {
                delete crtpReceived;
            }

            usleep(nMicrosecondsWait);
            crtpReceived = this->waitForPacket();
        }
    }

    if(bDeleteAfterwards)
    {
        delete crtpSend;
    }

    return crtpReturnvalue;
}

std::list<CCRTPPacket*> RadioCommunicationManager::popLoggingPackets()
{
    std::list<CCRTPPacket*> lstPackets = m_lstLoggingPackets;
    m_lstLoggingPackets.clear();

    return lstPackets;
}

bool RadioCommunicationManager::sendDummyPacket()
{
    CCRTPPacket *crtpReceived = NULL;
    CCRTPPacket *crtpDummy = new CCRTPPacket(0);
    crtpDummy->setIsPingPacket(true);

    crtpReceived = this->sendPacket(crtpDummy, true);

    if(crtpReceived)
    {
        delete crtpReceived;
        return true;
    }

    return false;
}
