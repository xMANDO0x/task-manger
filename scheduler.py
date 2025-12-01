# scheduler.py
"""
CPU Scheduling Algorithms Module
Provides different CPU scheduling algorithms for process management.
Note: This is a simulation/visualization - actual Windows CPU scheduling cannot be changed.
"""

from enum import Enum
from typing import List, Dict, Optional
import time

class SchedulingAlgorithm(Enum):
    """Enumeration of available CPU scheduling algorithms"""
    FCFS = "First Come First Served"
    SJF = "Shortest Job First"
    PRIORITY = "Priority"
    ROUND_ROBIN = "Round Robin"
    MULTILEVEL_QUEUE = "Multilevel Queue Scheduling"

class CPUScheduler:
    """CPU Scheduler class that implements various scheduling algorithms"""
    
    def __init__(self, algorithm: SchedulingAlgorithm = SchedulingAlgorithm.ROUND_ROBIN):
        self.algorithm = algorithm
        self.time_quantum = 10  # For Round Robin (milliseconds)
        self.current_time = 0
        # Track process arrival times and priorities
        self.process_arrival_times = {}  # PID -> arrival_time
        self.process_priorities = {}  # PID -> priority
        self.process_numbers = {}  # PID -> process_number
        self.process_counter = 0  # For numbering processes
        self.start_time = time.time()  # Reference time for arrival tracking
        
    def set_algorithm(self, algorithm: SchedulingAlgorithm):
        """Change the scheduling algorithm"""
        self.algorithm = algorithm
        
    def get_algorithm_name(self) -> str:
        """Get the name of the current algorithm"""
        return self.algorithm.value
        
    def schedule_processes(self, processes: List[Dict]) -> List[Dict]:
        """
        Schedule processes based on the selected algorithm.
        Also adds arrival time, priority, and process number to each process.
        
        Args:
            processes: List of process dictionaries with 'pid', 'name', 'cpu_percent', etc.
            
        Returns:
            Sorted list of processes according to the scheduling algorithm with added metadata
        """
        if not processes:
            return processes
        
        # Update arrival times and priorities for processes
        current_time = time.time()
        for process in processes:
            pid = process.get('pid', 0)
            
            # Track arrival time (first time we see this process)
            if pid not in self.process_arrival_times:
                self.process_arrival_times[pid] = current_time - self.start_time
                self.process_counter += 1
                self.process_numbers[pid] = self.process_counter
                process['process_number'] = self.process_counter
            else:
                # Use stored process number for existing processes
                process['process_number'] = self.process_numbers.get(pid, self.process_counter)
            
            # Assign priority based on CPU usage and process characteristics
            if pid not in self.process_priorities:
                cpu_percent = process.get('cpu_percent', 0)
                # Priority: 1-100, higher CPU = higher priority
                # But we'll also consider system processes
                priority = min(100, max(1, int(cpu_percent * 2) + 50))
                # System processes (high CPU) get higher priority
                if cpu_percent > 10:
                    priority = min(100, priority + 20)
                self.process_priorities[pid] = priority
            else:
                # Update priority dynamically based on current CPU usage
                cpu_percent = process.get('cpu_percent', 0)
                base_priority = min(100, max(1, int(cpu_percent * 2) + 50))
                if cpu_percent > 10:
                    base_priority = min(100, base_priority + 20)
                # Blend old and new priority (70% old, 30% new) for stability
                self.process_priorities[pid] = int(self.process_priorities[pid] * 0.7 + base_priority * 0.3)
            
            # Add arrival time and priority to process dict
            process['arrival_time'] = self.process_arrival_times[pid]
            process['priority'] = self.process_priorities[pid]
        
        # Clean up old processes that no longer exist
        current_pids = {p.get('pid', 0) for p in processes}
        self.process_arrival_times = {pid: time for pid, time in self.process_arrival_times.items() if pid in current_pids}
        self.process_priorities = {pid: pri for pid, pri in self.process_priorities.items() if pid in current_pids}
        self.process_numbers = {pid: num for pid, num in self.process_numbers.items() if pid in current_pids}
            
        if self.algorithm == SchedulingAlgorithm.FCFS:
            return self._fcfs(processes)
        elif self.algorithm == SchedulingAlgorithm.SJF:
            return self._sjf(processes)
        elif self.algorithm == SchedulingAlgorithm.PRIORITY:
            return self._priority(processes)
        elif self.algorithm == SchedulingAlgorithm.ROUND_ROBIN:
            return self._round_robin(processes)
        elif self.algorithm == SchedulingAlgorithm.MULTILEVEL_QUEUE:
            return self._multilevel_queue(processes)
        else:
            return processes
    
    def _fcfs(self, processes: List[Dict]) -> List[Dict]:
        """
        First Come First Served (FCFS)
        Processes are scheduled in the order they arrive (by arrival time)
        """
        # Sort by arrival time (earlier = first)
        return sorted(processes, key=lambda x: x.get('arrival_time', 0))
    
    def _sjf(self, processes: List[Dict]) -> List[Dict]:
        """
        Shortest Job First (SJF)
        Processes with lower CPU usage (shorter jobs) are scheduled first
        """
        # Sort by CPU percentage (lower = shorter job)
        return sorted(processes, key=lambda x: x.get('cpu_percent', 0))
    
    def _priority(self, processes: List[Dict]) -> List[Dict]:
        """
        Priority Scheduling
        Processes are scheduled based on priority (higher priority value = higher priority)
        """
        # Sort by priority descending (higher = higher priority)
        return sorted(processes, key=lambda x: x.get('priority', 0), reverse=True)
    
    def _round_robin(self, processes: List[Dict]) -> List[Dict]:
        """
        Round Robin Scheduling
        Processes are scheduled in a circular order with time quantum
        For display purposes, we rotate based on current time
        """
        if not processes:
            return processes
        # Rotate the list based on time quantum cycles
        rotation = (self.current_time // self.time_quantum) % len(processes)
        rotated = processes[rotation:] + processes[:rotation]
        # But still sort by CPU usage for better visualization
        return sorted(rotated, key=lambda x: x.get('cpu_percent', 0), reverse=True)
    
    def _multilevel_queue(self, processes: List[Dict]) -> List[Dict]:
        """
        Multilevel Queue Scheduling
        Processes are divided into multiple queues based on characteristics
        System processes (high CPU) get higher priority than user processes
        """
        # Separate into queues: System (high CPU) and User (low CPU)
        system_queue = [p for p in processes if p.get('cpu_percent', 0) > 5.0]
        user_queue = [p for p in processes if p.get('cpu_percent', 0) <= 5.0]
        
        # Sort each queue by CPU usage
        system_queue.sort(key=lambda x: x.get('cpu_percent', 0), reverse=True)
        user_queue.sort(key=lambda x: x.get('cpu_percent', 0), reverse=True)
        
        # System queue has higher priority
        return system_queue + user_queue
    
    def get_algorithm_description(self) -> str:
        """Get a description of the current algorithm"""
        descriptions = {
            SchedulingAlgorithm.FCFS: "Processes execute in order of arrival (by PID)",
            SchedulingAlgorithm.SJF: "Shortest jobs (lowest CPU usage) execute first",
            SchedulingAlgorithm.PRIORITY: "Higher priority processes (higher CPU) execute first",
            SchedulingAlgorithm.ROUND_ROBIN: "Processes execute in time slices (quantum-based)",
            SchedulingAlgorithm.MULTILEVEL_QUEUE: "System and user processes in separate queues"
        }
        return descriptions.get(self.algorithm, "Unknown algorithm")

