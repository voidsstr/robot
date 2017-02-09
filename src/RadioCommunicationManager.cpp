#include "RadioCommunicationManager.h"

RadioCommunicationManager::RadioCommunicationManager()
{
    //ctor
}

RadioCommunicationManager::~RadioCommunicationManager()
{
    //dtor
}

std::list<libusb_device*> RadioCommunicationManager::listDevices(int nVendorID, int nProductID)
{
    std::list<libusb_device*> lstDevices;
    ssize_t szCount;
    libusb_device **ptDevices;

    szCount = libusb_get_device_list(m_ctxContext, &ptDevices);

    for(unsigned int unI = 0; unI < szCount; unI++) {
        libusb_device *devCurrent = ptDevices[unI];
        libusb_device_descriptor ddDescriptor;

        libusb_get_device_descriptor(devCurrent, &ddDescriptor);

        if(ddDescriptor.idVendor == nVendorID && ddDescriptor.idProduct == nProductID) {
          libusb_ref_device(devCurrent);
          lstDevices.push_back(devCurrent);
        }
    }

    if(szCount > 0) {
        libusb_free_device_list(ptDevices, 1);
    }

    return lstDevices;
}
