# Правила совместимости комплектующих.
# Все проверки хранятся здесь, чтобы их было легко дополнять.
# Наполним на Этапе 3.

def check_cpu_motherboard(cpu, motherboard) -> bool:
    """Процессор и материнская плата — по сокету."""
    return cpu.socket == motherboard.socket


def check_cooler_cpu(cooler, cpu) -> bool:
    """Кулер должен поддерживать сокет CPU и выдерживать его TDP."""
    return cpu.socket in cooler.supported_sockets and cooler.max_tdp_watts >= cpu.tdp_watts
