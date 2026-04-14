import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 init failed")
    quit()

print("MT5 connected:", mt5.terminal_info())
print("Version:", mt5.version())

mt5.shutdown()
