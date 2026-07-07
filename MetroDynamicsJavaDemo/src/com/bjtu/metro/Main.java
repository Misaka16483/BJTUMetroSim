package com.bjtu.metro;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class Main {
    private Main() {
    }

    public static void main(String[] args) throws IOException {
        Path input = args.length > 0 ? Path.of(args[0]) : Path.of("data/stations.csv");
        Path output = args.length > 1 ? Path.of(args[1]) : Path.of("output/simulation_result.csv");
        Path gradesInput = args.length > 2 ? Path.of(args[2]) : Path.of("data/grades.csv");

        List<Station> stations = CsvStationLoader.load(input);
        GradeProfile gradeProfile = CsvGradeLoader.load(gradesInput);
        TrainParameters parameters = new TrainParameters(
                80.0,
                240_000.0,
                1.08,
                280_000.0,
                3_200_000.0,
                260_000.0,
                2_400_000.0,
                0.75,
                120_000.0,
                4_500.0,
                120.0,
                18.0,
                1.0
        );

        TrainSimulator simulator = new TrainSimulator(parameters, gradeProfile);
        List<SimulationPoint> points = simulator.simulate(stations);
        writeCsv(output, points);
        printSummary(stations, points, output);
    }

    private static void writeCsv(Path output, List<SimulationPoint> points) throws IOException {
        Files.createDirectories(output.getParent());
        List<String> lines = new ArrayList<>();
        lines.add("time_s,position_m,speed_mps,speed_kmh,acceleration_mps2,grade_promille,traction_force_n,brake_force_n,resistance_force_n,traction_energy_kwh,regen_energy_kwh,auxiliary_energy_kwh,net_energy_kwh,phase,from_station,to_station");
        for (SimulationPoint point : points) {
            lines.add(point.toCsvLine());
        }
        Files.write(output, lines, StandardCharsets.UTF_8);
    }

    private static void printSummary(List<Station> stations, List<SimulationPoint> points, Path output) {
        double totalTime = points.isEmpty() ? 0.0 : points.get(points.size() - 1).timeSeconds();
        double totalDistance = stations.get(stations.size() - 1).mileageMeters() - stations.get(0).mileageMeters();
        double averageSpeedKmh = totalTime > 0 ? totalDistance / totalTime * 3.6 : 0.0;
        SimulationPoint last = points.get(points.size() - 1);
        double netEnergy = last.tractionEnergyKWh() + last.auxiliaryEnergyKWh() - last.regenEnergyKWh();

        System.out.println("轨道交通车辆动力学仿真完成");
        System.out.printf("线路: %s -> %s%n", stations.get(0).name(), stations.get(stations.size() - 1).name());
        System.out.printf("距离: %.3f km%n", totalDistance / 1000.0);
        System.out.printf("总运行时间: %.1f s (%.1f min)%n", totalTime, totalTime / 60.0);
        System.out.printf("含停站平均速度: %.2f km/h%n", averageSpeedKmh);
        System.out.printf("牵引电能: %.2f kWh%n", last.tractionEnergyKWh());
        System.out.printf("再生制动回收: %.2f kWh%n", last.regenEnergyKWh());
        System.out.printf("辅助系统能耗: %.2f kWh%n", last.auxiliaryEnergyKWh());
        System.out.printf("净电能消耗: %.2f kWh%n", netEnergy);
        System.out.println("结果文件: " + output.toAbsolutePath());
    }
}
