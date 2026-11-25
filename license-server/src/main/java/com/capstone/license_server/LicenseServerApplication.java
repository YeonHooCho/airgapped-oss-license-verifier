package com.capstone.license_server;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "com.capstone")
public class LicenseServerApplication {

    public static void main(String[] args) {
        SpringApplication.run(LicenseServerApplication.class, args);
    }

}