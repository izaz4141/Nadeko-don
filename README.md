# Nadeko~don 

![Extension Icon](assets/nadeko-don.png)


Downloader built with QT

## Overview
This solution provides a robust desktop application with browser integration capabilities, enabling streamlined downloading of video content from various web sources. The tool supports modern streaming protocols while offering granular control over transfer parameters for optimized performance.


## Features

- **Multi-Protocol Support**: Download content via HTTP and HLS streaming protocols
- **Performance Control**: Configurable maximum download speed and concurrent transfer limits
- **Plugin Architecture**: Extensible functionality through YT-DLP plugin integration
- **Browser Integration**: Native Firefox support for direct capture of media streams
- **Transfer Management**: Real-time monitoring and control of active downloads

## Requirements

 - **Runtime**: Qt 6 Framework (minimum version 6.4 recommended)
 - **Platform**: Windows 10/11, macOS 12+, or Linux with X11/Wayland
 - **Browser Compatibility**: Firefox 100+ (for extension functionality)
 - **Dependencies**: Python 3.9+ runtime environment


## Installation

### Binary Distribution
1. Download the latest precompiled executable from [**GitHub Releases**](https://github.com/izaz4141/Nadeko-don/releases/latest/)
2. Execute the installer package
3. (Optional) Add application directory to system PATH for command-line access

### Source Compilation
```
# Clone repository
git clone https://github.com/izaz4141/Nadeko-don.git
cd Nadeko-don

# Install Python dependencies
pip install pyinstaller pyinstaller-hooks-contrib pyside requests

# Build executable
pyinstaller build.spec
```

## Development Roadmap

### High Priority
1. Enhanced UI/UX with professional styling and dark/light themes
2. Browser extension communication protocol implementation
3. Download progress visualization and analytics

## Planned Enhancements
1. Dynamic download part configuration during transfers
2. Download scheduling and queue management
3. Multiple downloads at once support
4. Automatic update mechanism