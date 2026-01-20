# -*- coding: utf-8 -*-
"""
MCP Control Plugin - Adds GUI buttons to control MCP
"""

from abaqusGui import *
from abaqusConstants import ALL
import os


class MCPStartForm(AFXForm):
    """Form to start MCP loop"""
    def __init__(self, owner):
        AFXForm.__init__(self, owner)
        
    def activate(self):
        # Import from __main__ where mcp_loop is defined
        sendCommand('import __main__; __main__.mcp_loop()')
        return True


class MCPStopForm(AFXForm):
    """Form to stop MCP loop"""
    def __init__(self, owner):
        AFXForm.__init__(self, owner)
        
    def activate(self):
        sendCommand('import __main__; __main__.mcp_stop()')
        return True


# Get the plugin toolset
toolset = getAFXApp().getAFXMainWindow().getPluginToolset()

# Register Start MCP button
toolset.registerGuiMenuButton(
    buttonText='MCP|Start MCP',
    object=MCPStartForm(toolset),
    messageId=AFXMode.ID_ACTIVATE,
    icon=None,
    kernelInitString='',
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Start the MCP loop',
    helpUrl=''
)

# Register Stop MCP button
toolset.registerGuiMenuButton(
    buttonText='MCP|Stop MCP',
    object=MCPStopForm(toolset),
    messageId=AFXMode.ID_ACTIVATE,
    icon=None,
    kernelInitString='',
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Stop the MCP loop',
    helpUrl=''
)
