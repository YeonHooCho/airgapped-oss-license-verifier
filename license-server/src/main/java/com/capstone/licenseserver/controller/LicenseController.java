package com.capstone.licenseserver.controller;

import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.*;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.Map;

@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "http://localhost:3000") // React(3000 포트) 통신 허용
public class LicenseController {

    private final RestTemplate restTemplate;
    private final String FASTAPI_URL = "http://localhost:8000/analyze";

    public LicenseController() {
        // ZIP 다중 파일 및 LLM 순차 분석 대기를 위한 120초 타임아웃
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10000);
        factory.setReadTimeout(180000); // 120초
        this.restTemplate = new RestTemplate(factory);
    }

    @PostMapping("/upload")
    public ResponseEntity<?> uploadFile(@RequestParam("file") MultipartFile file) {
        if (file.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("status", "error", "message", "업로드된 파일이 비어 있습니다."));
        }

        try {
            // FastAPI /analyze 엔드포인트에 맞춘 Multipart 데이터 패키징
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.MULTIPART_FORM_DATA);

            ByteArrayResource fileResource = new ByteArrayResource(file.getBytes()) {
                @Override
                public String getFilename() {
                    return file.getOriginalFilename();
                }
            };

            MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
            body.add("file", fileResource);

            HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body, headers);

            // FastAPI 호출 및 응답 JSON 전달
            ResponseEntity<Map> response = restTemplate.postForEntity(FASTAPI_URL, requestEntity, Map.class);
            return ResponseEntity.ok(response.getBody());

        } catch (IOException e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("status", "error", "message", "파일 읽기 실패: " + e.getMessage()));
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("status", "error", "message", "FastAPI 분석 엔진 통신 오류: " + e.getMessage()));
        }
    }
}