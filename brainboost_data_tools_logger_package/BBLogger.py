#!/usr/bin/env python3
"""
BBLogger module for brainboost_data_source_logger_package
"""

import logging
import os
import sys
from datetime import datetime

class BBLogger:
    """Simple logger class to replace the missing BBLogger from brainboost_data_source_logger_package"""
    
    _logger = None
    _initialized = False
    
    @classmethod
    def _initialize(cls, log_path='logs'):
        """Initialize the logger if not already done"""
        if cls._initialized:
            return
            
        # Create logs directory if it doesn't exist
        base_dir = os.path.dirname(os.path.dirname(__file__))
        log_dir = log_path if os.path.isabs(log_path) else os.path.join(base_dir, log_path)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Setup logger
        cls._logger = logging.getLogger('BBLogger')
        cls._logger.setLevel(logging.INFO)
        
        # Avoid adding handlers multiple times
        if not cls._logger.handlers:
            # File handler
            log_file = os.path.join(log_dir, 'brainboost.log')
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.INFO)
            
            # Console handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            
            # Formatter
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            cls._logger.addHandler(file_handler)
            cls._logger.addHandler(console_handler)
        
        cls._initialized = True
    
    @classmethod
    def log(cls, *args, **kwargs):
        """Log a message with INFO level"""
        log_path = kwargs.pop('log_path', 'logs')
        cls._initialize(log_path=log_path)
        message = ' '.join(str(arg) for arg in args)
        cls._logger.info(message)
    
    @classmethod
    def info(cls, *args, **kwargs):
        """Log a message with INFO level"""
        log_path = kwargs.pop('log_path', 'logs')
        cls._initialize(log_path=log_path)
        message = ' '.join(str(arg) for arg in args)
        cls._logger.info(message)
    
    @classmethod
    def warning(cls, *args, **kwargs):
        """Log a message with WARNING level"""
        log_path = kwargs.pop('log_path', 'logs')
        cls._initialize(log_path=log_path)
        message = ' '.join(str(arg) for arg in args)
        cls._logger.warning(message)
    
    @classmethod
    def error(cls, *args, **kwargs):
        """Log a message with ERROR level"""
        log_path = kwargs.pop('log_path', 'logs')
        cls._initialize(log_path=log_path)
        message = ' '.join(str(arg) for arg in args)
        cls._logger.error(message)
    
    @classmethod
    def debug(cls, *args, **kwargs):
        """Log a message with DEBUG level"""
        log_path = kwargs.pop('log_path', 'logs')
        cls._initialize(log_path=log_path)
        message = ' '.join(str(arg) for arg in args)
        cls._logger.debug(message)
    
    @classmethod
    def critical(cls, *args, **kwargs):
        """Log a message with CRITICAL level"""
        log_path = kwargs.pop('log_path', 'logs')
        cls._initialize(log_path=log_path)
        message = ' '.join(str(arg) for arg in args)
        cls._logger.critical(message)
