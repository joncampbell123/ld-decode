/************************************************************************

    configuration.cpp

    ld-process-efm - EFM data decoder
    Copyright (C) 2019 Simon Inns

    This file is part of ld-decode-tools.

    ld-process-efm is free software: you can redistribute it and/or
    modify it under the terms of the GNU General Public License as
    published by the Free Software Foundation, either version 3 of the
    License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.

************************************************************************/

#include "configuration.h"

// This define should be incremented if the settings file format changes
static const qint32 SETTINGSVERSION = 1;

Configuration::Configuration(QObject *parent) : QObject(parent)
{
    // Open the application's configuration file
    QString configurationPath;
    QString configurationFileName;

    configurationPath = QStandardPaths::writableLocation(QStandardPaths::ConfigLocation) ;
    configurationFileName = "ld-process-efm.ini" ;
    configuration = new QSettings(configurationPath + "/"+ configurationFileName, QSettings::IniFormat);

    // Read the configuration
    readConfiguration();

    // Are the configuration settings valid?
    if (settings.version != SETTINGSVERSION) {
        qDebug() << "Configuration::Configuration(): Configuration invalid or wrong version (" <<
                    settings.version << "!= " << SETTINGSVERSION <<").  Setting to default values";

        // Set default configuration
        setDefault();
    }
}

Configuration::~Configuration()
{
    delete configuration;
}

void Configuration::writeConfiguration(void)
{
    // Write the valid configuration flag
    configuration->setValue("version", settings.version);

    // Directories
    configuration->beginGroup("directories");
    configuration->setValue("sourceDirectory", settings.directories.sourceDirectory);
    configuration->setValue("audioDirectory", settings.directories.audioDirectory);
    configuration->setValue("dataDirectory", settings.directories.dataDirectory);
    configuration->endGroup();

    qDebug() << "Configuration::writeConfiguration(): sourceDirectory =" << settings.directories.sourceDirectory;
    qDebug() << "Configuration::writeConfiguration(): audioDirectory =" << settings.directories.audioDirectory;
    qDebug() << "Configuration::writeConfiguration(): dataDirectory =" << settings.directories.dataDirectory;

    // Windows
    configuration->beginGroup("windows");
    configuration->setValue("mainWindowGeometry", settings.windows.mainWindowGeometry);
    configuration->endGroup();

    // Sync the settings with disk
    qDebug() << "Configuration::writeConfiguration(): Writing configuration to disk";
    configuration->sync();
}

void Configuration::readConfiguration(void)
{
    qDebug() << "Configuration::readConfiguration(): Reading configuration from" << configuration->fileName();

    // Read the valid configuration flag
    settings.version = configuration->value("version").toInt();

    // Directories
    configuration->beginGroup("directories");
    settings.directories.sourceDirectory = configuration->value("sourceDirectory").toString();
    settings.directories.audioDirectory = configuration->value("audioDirectory").toString();
    settings.directories.dataDirectory = configuration->value("dataDirectory").toString();
    configuration->endGroup();

    qDebug() << "Configuration::readConfiguration(): sourceDirectory =" << settings.directories.sourceDirectory;
    qDebug() << "Configuration::readConfiguration(): audioDirectory =" << settings.directories.audioDirectory;
    qDebug() << "Configuration::readConfiguration(): dataDirectory =" << settings.directories.dataDirectory;

    // Windows
    configuration->beginGroup("windows");
    settings.windows.mainWindowGeometry = configuration->value("mainWindowGeometry").toByteArray();
    configuration->endGroup();
}

void Configuration::setDefault(void)
{
    // Set up the default values
    settings.version = SETTINGSVERSION;

    // Directories
    settings.directories.sourceDirectory = QDir::homePath();
    settings.directories.audioDirectory = QDir::homePath();
    settings.directories.dataDirectory = QDir::homePath();

    // Windows
    settings.windows.mainWindowGeometry = QByteArray();

    // Write the configuration
    writeConfiguration();
}

// Functions to get and set configuration values ----------------------------------------------------------------------

// Directories
void Configuration::setSourceDirectory(QString sourceDirectory)
{
    settings.directories.sourceDirectory = sourceDirectory;
}

QString Configuration::getSourceDirectory(void)
{
    return settings.directories.sourceDirectory;
}

void Configuration::setAudioDirectory(QString audioDirectory)
{
    settings.directories.audioDirectory = audioDirectory;
}

QString Configuration::getAudioDirectory(void)
{
    return settings.directories.audioDirectory;
}

void Configuration::setDataDirectory(QString dataDirectory)
{
    settings.directories.dataDirectory = dataDirectory;
}

QString Configuration::getDataDirectory(void)
{
    return settings.directories.dataDirectory;
}

// Windows
void Configuration::setMainWindowGeometry(QByteArray mainWindowGeometry)
{
    settings.windows.mainWindowGeometry = mainWindowGeometry;
}

QByteArray Configuration::getMainWindowGeometry(void)
{
    return settings.windows.mainWindowGeometry;
}
