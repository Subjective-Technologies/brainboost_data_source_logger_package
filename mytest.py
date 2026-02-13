from brainboost_data_source_logger_package.BBLogger import BBLogger
from brainboost_configuration_package.BBConfig import BBConfig

BBConfig.override('log_path','tests/logs')

BBLogger.log('Testing 1')
BBLogger.log('Testing for errors 2')
BBLogger.log('Testing warning 3')